#ifndef EXPORT_RESOLVER_H
#define EXPORT_RESOLVER_H

#include <stdint.h>
#include <mach-o/loader.h>
#include <mach-o/nlist.h>

#ifdef __cplusplus
extern "C" {
#endif

void *resolve_macho_export(const void *image_base, const char *symbol_name);
void *find_libmpv_initialize(void);

#ifdef __cplusplus
}
#endif

#endif /* EXPORT_RESOLVER_H */
