#define _DARWIN_C_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include "export_resolver.h"

typedef struct mpv_handle mpv_handle;

typedef int (*mpv_initialize_t)(mpv_handle *ctx);
typedef int (*mpv_set_option_string_t)(mpv_handle *ctx, const char *name, const char *data);

/* External symbol to be interposed at launch time via dyld */
extern int mpv_initialize(mpv_handle *ctx);

static void *g_libmpv = NULL;

static void *get_libmpv_handle(void) {
    if (!g_libmpv) {
        const char *custom_path = getenv("STREMIO_PATH");
        if (custom_path) {
            char dylib_path[1024];
            snprintf(dylib_path, sizeof(dylib_path), "%s/Contents/MacOS/libmpv.2.dylib", custom_path);
            g_libmpv = dlopen(dylib_path, RTLD_NOW | RTLD_NOLOAD);
            if (!g_libmpv) {
                g_libmpv = dlopen(dylib_path, RTLD_NOW);
            }
        }
        if (!g_libmpv) {
            g_libmpv = dlopen("/Applications/Stremio.app/Contents/MacOS/libmpv.2.dylib", RTLD_NOW | RTLD_NOLOAD);
        }
        if (!g_libmpv) {
            g_libmpv = dlopen("/Applications/Stremio.app/Contents/MacOS/libmpv.2.dylib", RTLD_NOW);
        }
        if (!g_libmpv) {
            g_libmpv = dlopen("libmpv.2.dylib", RTLD_NOW | RTLD_NOLOAD);
        }
        if (!g_libmpv) {
            g_libmpv = dlopen("libmpv.2.dylib", RTLD_NOW);
        }
    }
    return g_libmpv;
}

int mpv_initialize_hook(mpv_handle *ctx) {
    const char *socket_path = getenv("STREMIO_WP_SOCKET");
    if (!socket_path) {
        socket_path = "/tmp/stremio-watch-party.sock";
    }
    
    const char *debug = getenv("STREMIO_WP_DEBUG");
    if (!debug || debug[0] == '\0') {
        debug = getenv("WP_DEBUG");
    }
    if (debug && debug[0] != '\0') {
        fprintf(stderr, "[stremio-wp-shim] mpv_initialize_hook called. Setting input-ipc-server = %s\n", socket_path);
    }
    
    void *libmpv = get_libmpv_handle();
    if (libmpv) {
        mpv_set_option_string_t set_option = (mpv_set_option_string_t)dlsym(libmpv, "mpv_set_option_string");
        if (set_option) {
            int ret = set_option(ctx, "input-ipc-server", socket_path);
            fprintf(stderr, "[stremio-wp-shim] set input-ipc-server = %s (rc=%d)\n", socket_path, ret);
        } else {
            fprintf(stderr, "[stremio-wp-shim] Warning: could not resolve mpv_set_option_string via dlsym\n");
        }
    } else {
        fprintf(stderr, "[stremio-wp-shim] Warning: could not load libmpv handle in hook\n");
    }
    
    void *real_init_addr = find_libmpv_initialize();
    if (!real_init_addr) {
        fprintf(stderr, "[stremio-wp-shim] FATAL: Could not resolve real mpv_initialize!\n");
        return -1;
    }
    
    mpv_initialize_t real_init = (mpv_initialize_t)real_init_addr;
    return real_init(ctx);
}

typedef struct {
    const void *replacement;
    const void *replacee;
} interpose_tuple_t;

__attribute__((used)) static const interpose_tuple_t interposers[] __attribute__((section("__DATA,__interpose"))) = {
    { (const void *)mpv_initialize_hook, (const void *)mpv_initialize },
};
