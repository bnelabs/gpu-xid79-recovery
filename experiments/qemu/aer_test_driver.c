// SPDX-License-Identifier: GPL-2.0
/* Synthetic PCI testdev only. No NVIDIA hardware handling or GPU recovery. */
#include <linux/module.h>
#include <linux/pci.h>

static int lab_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
    int rc = pci_enable_device(pdev);
    if (!rc)
        pci_save_state(pdev);
    return rc;
}

static void lab_remove(struct pci_dev *pdev)
{
    pci_disable_device(pdev);
}

static pci_ers_result_t lab_error(struct pci_dev *pdev, pci_channel_state_t state)
{
    pci_info(pdev, "LAB_AER error_detected state=%d\n", state);
    if (state == pci_channel_io_perm_failure)
        return PCI_ERS_RESULT_DISCONNECT;
    pci_disable_device(pdev);
    return PCI_ERS_RESULT_NEED_RESET;
}

static pci_ers_result_t lab_reset(struct pci_dev *pdev)
{
    pci_info(pdev, "LAB_AER slot_reset\n");
    pci_restore_state(pdev);
    if (pci_enable_device(pdev))
        return PCI_ERS_RESULT_DISCONNECT;
    return PCI_ERS_RESULT_RECOVERED;
}

static void lab_resume(struct pci_dev *pdev)
{
    pci_info(pdev, "LAB_AER resume\n");
}

static const struct pci_error_handlers lab_errors = {
    .error_detected = lab_error,
    .slot_reset = lab_reset,
    .resume = lab_resume,
};
static const struct pci_device_id lab_ids[] = {
    { PCI_DEVICE(0x1b36, 0x0005) }, { 0 }
};
MODULE_DEVICE_TABLE(pci, lab_ids);
static struct pci_driver lab_driver = {
    .name = "aer_test_driver", .id_table = lab_ids,
    .probe = lab_probe, .remove = lab_remove, .err_handler = &lab_errors,
};
module_pci_driver(lab_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Guest AER experiment for QEMU synthetic PCI endpoints only");
