{
    "name": "Pinout MRP BoM Variant Matrix Generator",
    "summary": "Generate variant-specific BoM matrix lines from two product template attributes",
    "version": "17.0.1.0.0",
    "category": "Manufacturing",
    "author": "Pinout LTD",
    "license": "Other OSI approved licence",  # Apache-2.0
    "depends": [
        "mrp",
        "product",
        "uom",
        "mrp_bom_widget_section_and_note_one2many",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/mrp_bom_views.xml",
        "views/bom_variant_matrix_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
