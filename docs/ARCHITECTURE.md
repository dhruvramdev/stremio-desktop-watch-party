# System Architecture & Technical Specifications

Stremio Desktop Watch Party synchronizes playback across remote macOS participants by interfacing directly with the native embedded `libmpv` player inside `/Applications/Stremio.app`.

---

## 1. High-Level Architecture

```text
+-------------------------------------------------------------------------+
|                         watch-party launcher                            |
|  - Verifies Stremio app bundle, version, & codesign entitlements        |
|  - Creates isolated 0700 runtime directory (/tmp/stremio-wp-.../)       |
|  - Spawns Stremio with DYLD_INSERT_LIBRARIES=libstremio-mpv-ipc-shim.dylib|
|  - Launches controller daemon in background                             |
+------------------------------------+------------------------------------+
                                     |
               +---------------------+---------------------+
               | launches                                  | connects
               v                                           v
+-------------------------------+             +-----------------------------+
|    Stremio Desktop Process    |             |     Controller Daemon       |
|                               |             |                             |
|  +-------------------------+  |             |  - mpv_ipc: newline JSON    |
|  |   mpv_ipc_shim.dylib    |  |             |    Unix socket client       |
|  | - Mach-O export resolver|  |             |  - sync: drift correction,  |
|  | - hooks mpv_initialize  |  |             |    seek detect, echo-suppr  |
|  | - sets input-ipc-server |  |             |  - relay: authenticated     |
|  +------------+------------+  |             |    WebSocket connection     |
|               |               |             +--------------+--------------+
|               v               |                            |
|  +-------------------------+  |                            |
|  |      libmpv player      |<======= Unix Socket ==========+
|  +-------------------------+  |    /tmp/stremio-wp-.../mpv.sock
+-------------------------------+                            |
                                                             |
                                                             v wss://
                                              +-----------------------------+
                                              |     WebSocket Relay         |
                                              |  - Constant-time HMAC auth  |
                                              |  - Host control broadcast   |
                                              |  - No stream URLs or media  |
                                              +-----------------------------+
```

---

## 2. Invariants & Guarantees

1. **Zero Modifications to Stremio.app**: Never alters, patches, or re-signs `/Applications/Stremio.app`. Dynamic linker interposition is ephemeral and occurs exclusively at process spawn time.
2. **Absolute Privacy**: Zero torrent hashes, debrid tokens, stream URLs, file paths, subtitles, or addon details are ever sent to the relay. Only relative timing (`time_s`, `paused`, `duration_s`) and room coordination metadata traverse the wire.
3. **Fail-Closed Safety Gate**: If Stremio's version is unsupported or entitlements restrict dynamic linking (`allow-dyld-environment-variables`), the launcher aborts launch unless explicitly forced (`--force`).
4. **Host Authority Model**: Only designated room hosts can broadcast control events (`play`, `pause`, `seek`, `state`). Guests follow the host; rogue guest control attempts are dropped by the relay server.

---

## 3. Dynamic Mach-O Interposition Shim (`shim/`)

To enable an external controller to communicate with Stremio's embedded player without modifying the app binary, the native shim intercepts `mpv_initialize`:

1. **Mach-O `__DATA,__interpose`**:
   The dylib exports an interposition tuple replacing `_mpv_initialize` with `mpv_initialize_hook`.
2. **Dynamic Export Resolver (`export_resolver.c`)**:
   Rather than hardcoding image offsets, the resolver dynamically inspects the loaded Mach-O 64-bit header of `libmpv.2.dylib`:
   - Decodes the dyld export trie using ULEB128 decoding.
   - Falls back to `LC_SYMTAB` symbol tables.
   - Computes ASLR slide dynamically:
     $$\text{Address} = \text{ImageBase} + \text{ExportOffset}$$
3. **IPC Socket Injection**:
   Inside `mpv_initialize_hook`, before calling real `mpv_initialize`:
   ```c
   mpv_set_option_string(ctx, "input-ipc-server", getenv("STREMIO_WP_SOCKET"));
   ```

---

## 4. Playback Synchronization Engine (`controller/sync.py`)

- **State Normalization**:
  Maintains active playback properties: `paused`, `time_s`, `duration_s`, `seeking`, `buffering`, `media_title`, `drift_s`.
- **Local Seek Detection**:
  - When playing: detects a jump in `time-pos` $> 2.0$ seconds from expected linear progression ($t_{\text{expected}} = t_{\text{old}} + \Delta t$).
  - When paused: detects scrubbing movements where $|t_{\text{new}} - t_{\text{old}}| > 2.0$ seconds.
- **Echo Suppression State Machine**:
  When a remote sync command (seek or play/pause) is executed locally, an echo suppression entry is registered for 1.5 seconds or until the player arrives within $\pm 0.3$s of target time to avoid sending local echoes back to the relay.
- **Latency-Compensated Drift Correction**:
  - Outbound host messages timestamp playback using monotonic milliseconds (`sent_at_ms`).
  - Expected playback time at arrival:
    $$t_{\text{expected}} = t_{\text{host}} + \frac{\text{now}_{\text{ms}} - \text{sent\_at}_{\text{ms}}}{1000} \quad (\text{if not paused})$$
  - Drift calculation:
    $$\text{drift} = t_{\text{local}} - t_{\text{expected}}$$
  - If $|\text{drift}| > 1.5\text{s}$, an automated seek correction is dispatched.
  - If $\text{local\_paused} \neq \text{host\_paused}$, pause state is enforced.

---

## 5. WebSocket Relay Protocol (`relay/`)

- **Authentication**: Constant-time comparison (`hmac.compare_digest`) validates the room pre-shared secret.
- **Message Types**:
  - `hello`: Handshake and role request.
  - `welcome`: Room client list and assigned authority role.
  - `join` / `leave`: Presence events.
  - `play` / `pause`: Playback state toggles.
  - `seek`: Timestamp position jump.
  - `state`: Host periodic heartbeat (every 3 seconds) broadcasting `time_s`, `paused`, and `duration_s`.

