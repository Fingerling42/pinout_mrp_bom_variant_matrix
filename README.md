# Pinout MRP BoM Variant Matrix Generator

Odoo 17 addon for generating variant-specific Bill of Materials lines from a
two-axis product template attribute matrix.

## Features

- Add a `Generate Variant Matrix` button on BoM forms.
- Build component mappings from one product template attribute axis.
- Build quantity mappings from a second product template attribute axis.
- Preview every generated combination before applying it.
- Create, update, or replace generated BoM lines safely.
- Store lightweight metadata on generated BoM lines for future updates.
- Use `product.template.attribute.value` records for Apply on Variants.
- Optionally create section lines for the component axis.

## Dependencies

- mrp
- product
- uom
- mrp_bom_widget_section_and_note_one2many

## Installation

Add the module to an Odoo addons path, update the Apps list, and install
Pinout MRP BoM Variant Matrix Generator.

## Usage

Open a template-level BoM and click `Generate Variant Matrix`.

Select one product template attribute for the component axis and a different
attribute for the quantity axis. Fill the component and quantity mapping tabs,
click `Preview`, then apply the matrix when the preview has no errors.

The generator does not change BoM explosion, manufacturing orders, stock moves,
costing, products, attributes, or product variants.

## License

Apache-2.0
