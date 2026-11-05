#define _DARWIN_C_SOURCE
#include "export_resolver.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>
#include <mach-o/dyld.h>

static uint64_t read_uleb128_local(const uint8_t **p, const uint8_t *end) {
    uint64_t result = 0;
    int bit = 0;
    while (*p < end) {
        uint64_t slice = **p;
        (*p)++;
        result |= (slice & 0x7f) << bit;
        if (!(slice & 0x80)) {
            break;
        }
        bit += 7;
    }
    return result;
}

/* Recursive or iterative trie walker */
static int walk_export_trie(const uint8_t *trie_start, const uint8_t *trie_end,
                            const uint8_t *node, const char *symbol_name,
                            char *prefix, uint32_t prefix_len, uint32_t max_len,
                            uint64_t *out_offset) {
    if (!node || node >= trie_end) return 0;
    
    const uint8_t *p = node;
    uint64_t terminal_size = read_uleb128_local(&p, trie_end);
    const uint8_t *children_node = p + terminal_size;
    
    if (terminal_size > 0) {
        const uint8_t *term_p = p;
        uint64_t flags = read_uleb128_local(&term_p, trie_end);
        if (!(flags & 0x08) && !(flags & 0x10)) { /* Not reexport or stub */
            uint64_t sym_offset = read_uleb128_local(&term_p, trie_end);
            prefix[prefix_len] = '\0';
            if (strcmp(prefix, symbol_name) == 0) {
                *out_offset = sym_offset;
                return 1;
            }
        }
    }
    
    if (children_node >= trie_end) return 0;
    
    uint8_t child_count = *children_node;
    const uint8_t *p_child = children_node + 1;
    
    for (uint8_t i = 0; i < child_count; i++) {
        if (p_child >= trie_end) break;
        uint32_t len = 0;
        const uint8_t *s = p_child;
        while (p_child < trie_end && *p_child != '\0') {
            p_child++;
            len++;
        }
        if (p_child < trie_end) p_child++; /* skip '\0' */
        
        uint64_t child_node_offset = read_uleb128_local(&p_child, trie_end);
        if (child_node_offset == 0) continue;

        if (prefix_len + len >= max_len) continue;
        if (strncmp(symbol_name + prefix_len, (const char *)s, len) != 0) continue;

        memcpy(prefix + prefix_len, s, len);
        
        if (walk_export_trie(trie_start, trie_end, trie_start + child_node_offset,
                             symbol_name, prefix, prefix_len + len, max_len, out_offset)) {
            return 1;
        }
    }
    
    return 0;
}

void *resolve_macho_export(const void *image_base, const char *symbol_name) {
    if (!image_base || !symbol_name) return NULL;
    
    const struct mach_header_64 *hdr = (const struct mach_header_64 *)image_base;
    if (hdr->magic != MH_MAGIC_64) {
        return NULL;
    }
    
    const uint8_t *cmd_ptr = (const uint8_t *)image_base + sizeof(struct mach_header_64);
    
    const struct segment_command_64 *text_seg = NULL;
    const struct segment_command_64 *linkedit_seg = NULL;
    const struct symtab_command *symtab_cmd = NULL;
    
    uint32_t export_off = 0;
    uint32_t export_size = 0;
    
    for (uint32_t i = 0; i < hdr->ncmds; i++) {
        const struct load_command *lc = (const struct load_command *)cmd_ptr;
        if (lc->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *seg = (const struct segment_command_64 *)lc;
            if (strcmp(seg->segname, "__TEXT") == 0) {
                text_seg = seg;
            } else if (strcmp(seg->segname, "__LINKEDIT") == 0) {
                linkedit_seg = seg;
            }
        } else if (lc->cmd == LC_SYMTAB) {
            symtab_cmd = (const struct symtab_command *)lc;
        } else if (lc->cmd == LC_DYLD_INFO || lc->cmd == LC_DYLD_INFO_ONLY) {
            const struct dyld_info_command *dyld_info = (const struct dyld_info_command *)lc;
            export_off = dyld_info->export_off;
            export_size = dyld_info->export_size;
        }
#ifdef LC_DYLD_EXPORTS_TRIE
        else if (lc->cmd == LC_DYLD_EXPORTS_TRIE) {
            const struct linkedit_data_command *extrie = (const struct linkedit_data_command *)lc;
            export_off = extrie->dataoff;
            export_size = extrie->datasize;
        }
#endif
        cmd_ptr += lc->cmdsize;
    }
    
    if (!text_seg || !linkedit_seg) {
        return NULL;
    }
    
    uintptr_t slide = (uintptr_t)image_base - (uintptr_t)text_seg->vmaddr;
    const uint8_t *linkedit_base = (const uint8_t *)(linkedit_seg->vmaddr + slide - linkedit_seg->fileoff);
    
    /* 1. Try Export Trie */
    if (export_off > 0 && export_size > 0) {
        const uint8_t *trie_start = linkedit_base + export_off;
        const uint8_t *trie_end = trie_start + export_size;
        char prefix[1024];
        uint64_t sym_offset = 0;
        if (walk_export_trie(trie_start, trie_end, trie_start, symbol_name, prefix, 0, sizeof(prefix), &sym_offset)) {
            return (void *)((uintptr_t)image_base + sym_offset);
        }
    }
    
    /* 2. Fallback to LC_SYMTAB */
    if (symtab_cmd) {
        const struct nlist_64 *syms = (const struct nlist_64 *)(linkedit_base + symtab_cmd->symoff);
        const char *strings = (const char *)(linkedit_base + symtab_cmd->stroff);
        
        for (uint32_t i = 0; i < symtab_cmd->nsyms; i++) {
            if (syms[i].n_un.n_strx != 0) {
                const char *sym_name = strings + syms[i].n_un.n_strx;
                if (strcmp(sym_name, symbol_name) == 0) {
                    if (syms[i].n_value != 0) {
                        return (void *)((uintptr_t)image_base + syms[i].n_value);
                    }
                }
            }
        }
    }
    
    return NULL;
}

void *find_libmpv_initialize(void) {
    void *libmpv = NULL;
    const char *custom_path = getenv("STREMIO_PATH");
    if (custom_path) {
        char dylib_path[1024];
        snprintf(dylib_path, sizeof(dylib_path), "%s/Contents/MacOS/libmpv.2.dylib", custom_path);
        libmpv = dlopen(dylib_path, RTLD_NOW | RTLD_NOLOAD);
        if (!libmpv) {
            libmpv = dlopen(dylib_path, RTLD_NOW);
        }
    }
    if (!libmpv) {
        libmpv = dlopen("/Applications/Stremio.app/Contents/MacOS/libmpv.2.dylib", RTLD_NOW | RTLD_NOLOAD);
    }
    if (!libmpv) {
        libmpv = dlopen("/Applications/Stremio.app/Contents/MacOS/libmpv.2.dylib", RTLD_NOW);
    }
    if (!libmpv) {
        libmpv = dlopen("libmpv.2.dylib", RTLD_NOW | RTLD_NOLOAD);
    }
    if (!libmpv) {
        libmpv = dlopen("libmpv.2.dylib", RTLD_NOW);
    }
    if (!libmpv) {
        return NULL;
    }
    
    void *set_opt_sym = dlsym(libmpv, "mpv_set_option_string");
    if (!set_opt_sym) {
        return NULL;
    }
    
    Dl_info info;
    if (dladdr(set_opt_sym, &info) == 0 || !info.dli_fbase) {
        return NULL;
    }
    
    void *init_fn = resolve_macho_export(info.dli_fbase, "_mpv_initialize");
    return init_fn;
}
