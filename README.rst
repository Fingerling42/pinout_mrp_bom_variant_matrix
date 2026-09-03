========================================
Pinout MRP BoM Variant Matrix Generator
========================================

This Odoo 17 addon generates variant-specific Bill of Materials lines from a
one- or two-axis product template attribute matrix.

Features
========

* Adds a ``Generate Variant Matrix`` button to BoM forms.
* Adds a ``Generate Variant Components`` button for multi-level variant
  assemblies.
* Builds component mappings from a product template attribute axis.
* Generates a one-axis matrix with a constant quantity when no quantity axis
  is selected.
* Builds quantity mappings from an optional second product template attribute
  axis.
* Supports standalone component products without attributes.
* Previews every generated combination before applying it.
* Creates, updates, or replaces generated BoM lines safely.
* Stores lightweight metadata on generated BoM lines for future updates.
* Uses ``product.template.attribute.value`` records for ``Apply on Variants``.
* Optionally creates section lines for the component axis.

Dependencies
============

* ``mrp``
* ``product``
* ``uom``
* ``mrp_bom_widget_section_and_note_one2many``

Installation
============

Add the module to an Odoo addons path, update the Apps list, and install
``Pinout MRP BoM Variant Matrix Generator``.

Usage
=====

Variant matrix
--------------

Open a template-level BoM and click ``Generate Variant Matrix``.

Select one product template attribute for the component axis and map each of
its values to a component product.

For a one-axis matrix, leave ``Quantity Axis Attribute`` empty and enter a
``Default Quantity``. The generator creates one BoM line for every component
axis value. Each line applies to the matching finished-product variants, while
the selected component may be a standalone product without attributes.

If quantities vary by a second attribute, select that attribute for
``Quantity Axis Attribute`` and fill the ``Quantity Mapping`` tab. The
generator then creates a line for every component-axis and quantity-axis
combination.

Click ``Preview`` and review the generated rows. When the preview contains no
errors, click ``Apply Matrix``.

Variant components
------------------

For upper-level assemblies made from variant-specific components, open a
template-level BoM and click ``Generate Variant Components``.

Add one component rule per component template and map parent attributes to
component attributes. For example, a component that follows only the parent
color needs one mapping, while another component may map both color and
emotion. The preview resolves the matching component variant and creates BoM
lines with the appropriate parent ``Apply on Variants`` values.

If a component template does not contain every variant combination, set
``Missing Component Variants`` to ``Skip missing variants``. Missing variants
remain visible as skipped preview rows and do not block valid generated lines.

Limitations
===========

The generator does not change BoM explosion, manufacturing orders, stock
moves, costing, products, attributes, or product variants.

License
=======

Apache-2.0
