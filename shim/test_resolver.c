#define _DARWIN_C_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <assert.h>
#include "export_resolver.h"
#include <mach-o/dyld.h>

int main(void) {
    printf("[*] Running test_resolver...\n");
    
    void *libmpv = dlopen("/Applications/Stremio.app/Contents/MacOS/libmpv.2.dylib", RTLD_NOW);
    if (!libmpv) {
        fprintf(stderr, "[-] Failed to dlopen libmpv.2.dylib: %s\n", dlerror());
        return 1;
    }
    printf("[+] dlopened libmpv.2.dylib at %p\n", libmpv);
    
    void *set_opt = dlsym(libmpv, "mpv_set_option_string");
    assert(set_opt != NULL);
    
    Dl_info info;
    int res = dladdr(set_opt, &info);
    assert(res != 0);
    printf("[+] libmpv image base (dli_fbase): %p (%s)\n", info.dli_fbase, info.dli_fname);
    
    void *init_fn = resolve_macho_export(info.dli_fbase, "_mpv_initialize");
    printf("[+] Resolved _mpv_initialize: %p\n", init_fn);
    assert(init_fn != NULL);
    
    uintptr_t expected_offset = 0xb8994;
    uintptr_t actual_offset = (uintptr_t)init_fn - (uintptr_t)info.dli_fbase;
    printf("[*] Expected offset: 0x%lx, Actual offset: 0x%lx\n", expected_offset, actual_offset);
    
    /* Also test find_libmpv_initialize() */
    void *found_init = find_libmpv_initialize();
    printf("[+] find_libmpv_initialize() returned: %p\n", found_init);
    assert(found_init == init_fn);
    
    printf("[+] SUCCESS: All assertions passed!\n");
    return 0;
}
