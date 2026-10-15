/* Deliberately nonfunctional NVIDIA-identity PCI fixture; no ASIC emulation. */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include "libvfio-user.h"

static unsigned long reads, writes, resets;
static ssize_t access_bar(vfu_ctx_t *ctx, char *buffer, size_t count,
                          loff_t offset, bool write)
{
    (void)ctx; (void)offset;
    if (write) writes++;
    else { reads++; memset(buffer, 0xff, count); }
    return count;
}
static int reset_device(vfu_ctx_t *ctx, vfu_reset_type_t type)
{
    (void)ctx; (void)type; resets++;
    return 0; /* A protocol acknowledgment, never a claim of hardware recovery. */
}
int main(int argc, char **argv)
{
    if (argc != 3) { fprintf(stderr, "Usage: dead-endpoint SOCKET DEVICE_ID_HEX\n"); return 2; }
    char *end;
    unsigned long id = strtoul(argv[2], &end, 16);
    if (*end || id > 65535) return 2;
    vfu_ctx_t *ctx = vfu_create_ctx(VFU_TRANS_SOCK, argv[1], 0, NULL, VFU_DEV_TYPE_PCI);
    if (!ctx) { perror("create"); return 1; }
    if (vfu_pci_init(ctx, VFU_PCI_TYPE_CONVENTIONAL, 0, 0) < 0) { perror("pci_init"); return 1; }
    vfu_pci_set_id(ctx, 0x10de, id, 0x10de, id);
    vfu_pci_set_class(ctx, 3, 0, 0);
    ((uint8_t *)vfu_pci_get_config_space(ctx))[0x3d] = 1;
    int flags = VFU_REGION_FLAG_RW | VFU_REGION_FLAG_MEM;
    if (vfu_setup_region(ctx, VFU_PCI_DEV_BAR0_REGION_IDX, 16UL<<20, access_bar, flags, NULL, 0, -1, 0) < 0 ||
        vfu_setup_region(ctx, VFU_PCI_DEV_BAR1_REGION_IDX, 256UL<<20, access_bar, flags, NULL, 0, -1, 0) < 0 ||
        vfu_setup_device_nr_irqs(ctx, VFU_DEV_INTX_IRQ, 1) < 0 ||
        vfu_setup_device_reset_cb(ctx, reset_device) < 0 || vfu_realize_ctx(ctx) < 0) {
        perror("setup"); return 1;
    }
    puts("DEAD_ENDPOINT_READY"); fflush(stdout);
    if (vfu_attach_ctx(ctx) < 0) { perror("attach"); return 1; }
    while (vfu_run_ctx(ctx) >= 0 || errno == EINTR) { }
    fprintf(stderr, "reads=%lu writes=%lu reset_acknowledgments=%lu\n", reads, writes, resets);
    vfu_destroy_ctx(ctx);
    return 0;
}
