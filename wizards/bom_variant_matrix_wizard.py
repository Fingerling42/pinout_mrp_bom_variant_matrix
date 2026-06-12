import re

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class MrpBomVariantMatrixWizard(models.TransientModel):
    _name = "mrp.bom.variant.matrix.wizard"
    _description = "BoM Variant Matrix Generator"

    bom_id = fields.Many2one(
        comodel_name="mrp.bom",
        string="BoM",
        required=True,
    )
    product_tmpl_id = fields.Many2one(
        related="bom_id.product_tmpl_id",
        string="Product Template",
        readonly=True,
    )
    component_axis_attribute_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Component Axis Attribute",
        required=True,
    )
    quantity_axis_attribute_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Quantity Axis Attribute",
        required=True,
    )
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="UoM",
        required=True,
    )
    create_sections = fields.Boolean(
        string="Create Sections",
        default=True,
    )
    section_by_component_axis = fields.Boolean(
        string="Section by Component Axis",
        default=True,
    )
    mode = fields.Selection(
        selection=[
            ("append", "Append only"),
            ("update", "Update matching lines"),
            ("replace", "Replace generated block"),
        ],
        default="append",
        required=True,
    )
    matrix_key = fields.Char(
        string="Matrix Key",
        default="variant_matrix",
        required=True,
    )
    sequence_start = fields.Integer(
        string="Sequence Start",
        default=1000,
        required=True,
    )
    sequence_step = fields.Integer(
        string="Sequence Step",
        default=10,
        required=True,
    )
    component_mapping_line_ids = fields.One2many(
        comodel_name="mrp.bom.variant.matrix.component.line",
        inverse_name="wizard_id",
        string="Component Mapping",
    )
    quantity_mapping_line_ids = fields.One2many(
        comodel_name="mrp.bom.variant.matrix.quantity.line",
        inverse_name="wizard_id",
        string="Quantity Mapping",
    )
    preview_line_ids = fields.One2many(
        comodel_name="mrp.bom.variant.matrix.preview.line",
        inverse_name="wizard_id",
        string="Preview",
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        bom_id = values.get("bom_id") or self.env.context.get("default_bom_id")
        if bom_id and not values.get("product_uom_id"):
            bom = self.env["mrp.bom"].browse(bom_id)
            if bom.product_tmpl_id:
                values["product_uom_id"] = bom.product_tmpl_id.uom_id.id
        return values

    @api.onchange("bom_id")
    def _onchange_bom_id(self):
        for wizard in self:
            if wizard.bom_id and wizard.bom_id.product_tmpl_id:
                wizard.product_uom_id = wizard.bom_id.product_tmpl_id.uom_id
            wizard.component_mapping_line_ids = [fields.Command.clear()]
            wizard.quantity_mapping_line_ids = [fields.Command.clear()]
            wizard.preview_line_ids = [fields.Command.clear()]

    @api.onchange("component_axis_attribute_id", "quantity_axis_attribute_id")
    def _onchange_axis_attribute_ids(self):
        for wizard in self:
            if (
                wizard.component_axis_attribute_id
                and wizard.quantity_axis_attribute_id
                and wizard.component_axis_attribute_id
                == wizard.quantity_axis_attribute_id
            ):
                return {
                    "warning": {
                        "title": "Invalid matrix axes",
                        "message": "Component and quantity axes must be different.",
                    }
                }

            if wizard.component_axis_attribute_id and wizard.quantity_axis_attribute_id:
                generated_key = wizard._make_matrix_key()
                if not wizard.matrix_key or wizard.matrix_key == "variant_matrix":
                    wizard.matrix_key = generated_key

            wizard._sync_component_mapping_lines()
            wizard._sync_quantity_mapping_lines()
            wizard.preview_line_ids = [fields.Command.clear()]

    def action_preview(self):
        self.ensure_one()
        self._rebuild_preview()
        return self._reload_action()

    def action_apply_matrix(self):
        self.ensure_one()
        self._rebuild_preview()
        error_lines = self.preview_line_ids.filtered(
            lambda line: line.status == "error"
        )
        if error_lines:
            raise UserError("\n".join(error_lines.mapped("message")))

        sequence = self.sequence_start
        if self.mode == "replace":
            self._replace_generated_lines()

        for component_line in self._ordered_component_lines():
            component_preview_lines = self._get_component_preview_lines(
                component_line.ptav_id
            )
            product_preview_lines = component_preview_lines.filtered(
                lambda line: line.status in ("new", "update")
            )
            if not product_preview_lines:
                continue

            if self.create_sections and self.section_by_component_axis:
                section_vals = self._prepare_section_line_vals(
                    component_line.ptav_id, sequence
                )
                section_line = self._find_generated_section_line(component_line.ptav_id)
                if section_line:
                    section_line.write(section_vals)
                else:
                    self.env["mrp.bom.line"].create(section_vals)
                sequence += self.sequence_step

            for quantity_line in self._ordered_quantity_lines():
                preview_line = self._find_preview_line(
                    component_line.ptav_id, quantity_line.ptav_id
                )
                if not preview_line or preview_line.status == "skip_duplicate":
                    continue

                vals = self._prepare_product_line_vals(
                    component_line,
                    quantity_line,
                    sequence,
                )
                if (
                    preview_line.status == "update"
                    and preview_line.existing_bom_line_id
                ):
                    preview_line.existing_bom_line_id.write(vals)
                else:
                    self.env["mrp.bom.line"].create(vals)
                sequence += self.sequence_step

        return {"type": "ir.actions.act_window_close"}

    def _reload_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Generate Variant Matrix",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _validate_configuration(self):
        self.ensure_one()
        if not self.bom_id:
            raise UserError("Select a BoM first.")
        if not self.bom_id.product_tmpl_id:
            raise UserError("The selected BoM has no product template.")
        if self.bom_id.product_id:
            raise UserError(
                "Variant matrix generation is intended for template BoMs. "
                "The selected BoM is restricted to a single product variant."
            )
        if not self.component_axis_attribute_id or not self.quantity_axis_attribute_id:
            raise UserError("Select both matrix axis attributes.")
        if self.component_axis_attribute_id == self.quantity_axis_attribute_id:
            raise UserError("Component and quantity axes must be different.")
        if not self.product_uom_id:
            raise UserError("Select a product unit of measure.")
        if not self.matrix_key:
            raise UserError("Matrix key is required.")
        if self.sequence_step <= 0:
            raise UserError("Sequence step must be greater than zero.")

        missing = (
            self.component_axis_attribute_id + self.quantity_axis_attribute_id
        ).filtered(lambda attribute: not self._get_axis_ptavs(attribute))
        if missing:
            raise UserError(
                "The selected product template does not contain these attributes: %s"
                % ", ".join(missing.mapped("display_name"))
            )

    def _sync_component_mapping_lines(self):
        self.ensure_one()
        existing_by_ptav = {
            line.ptav_id.id: line
            for line in self.component_mapping_line_ids
            if line.ptav_id
        }
        commands = [fields.Command.clear()]
        for index, ptav in enumerate(
            self._get_axis_ptavs(self.component_axis_attribute_id), start=1
        ):
            existing = existing_by_ptav.get(ptav.id)
            commands.append(
                fields.Command.create(
                    {
                        "ptav_id": ptav.id,
                        "component_product_id": existing.component_product_id.id
                        if existing
                        else False,
                        "sequence": index * 10,
                    }
                )
            )
        self.component_mapping_line_ids = commands

    def _sync_quantity_mapping_lines(self):
        self.ensure_one()
        existing_by_ptav = {
            line.ptav_id.id: line
            for line in self.quantity_mapping_line_ids
            if line.ptav_id
        }
        commands = [fields.Command.clear()]
        for index, ptav in enumerate(
            self._get_axis_ptavs(self.quantity_axis_attribute_id), start=1
        ):
            existing = existing_by_ptav.get(ptav.id)
            commands.append(
                fields.Command.create(
                    {
                        "ptav_id": ptav.id,
                        "quantity": existing.quantity if existing else 0.0,
                        "sequence": index * 10,
                    }
                )
            )
        self.quantity_mapping_line_ids = commands

    def _get_axis_ptavs(self, attribute):
        self.ensure_one()
        if not self.product_tmpl_id or not attribute:
            return self.env["product.template.attribute.value"]
        return self.env["product.template.attribute.value"].search(
            [
                ("product_tmpl_id", "=", self.product_tmpl_id.id),
                ("attribute_id", "=", attribute.id),
            ]
        )

    def _rebuild_preview(self):
        self.ensure_one()
        self._validate_configuration()
        if not self.component_mapping_line_ids:
            self._sync_component_mapping_lines()
        if not self.quantity_mapping_line_ids:
            self._sync_quantity_mapping_lines()
        if not self.component_mapping_line_ids or not self.quantity_mapping_line_ids:
            raise UserError("Matrix mapping lines could not be built.")
        self.preview_line_ids.unlink()

        commands = []
        seen_combinations = set()
        for component_line in self._ordered_component_lines():
            for quantity_line in self._ordered_quantity_lines():
                key = (component_line.ptav_id.id, quantity_line.ptav_id.id)
                if key in seen_combinations:
                    commands.append(
                        fields.Command.create(
                            self._prepare_preview_error_vals(
                                component_line,
                                quantity_line,
                                "Duplicate preview combination.",
                            )
                        )
                    )
                    continue
                seen_combinations.add(key)
                commands.append(
                    fields.Command.create(
                        self._prepare_preview_line_vals(component_line, quantity_line)
                    )
                )
        self.preview_line_ids = commands

    def _prepare_preview_line_vals(self, component_line, quantity_line):
        target_ptav_ids = [component_line.ptav_id.id, quantity_line.ptav_id.id]
        status, message, existing_line = self._get_preview_status(target_ptav_ids)
        if not component_line.component_product_id:
            status = "error"
            message = "Missing component product for %s." % component_line.ptav_id.name
        elif not quantity_line.quantity or quantity_line.quantity <= 0:
            status = "error"
            message = (
                "Quantity must be greater than zero for %s."
                % quantity_line.ptav_id.name
            )
        elif (
            self.product_uom_id.category_id
            != component_line.component_product_id.uom_id.category_id
        ):
            status = "error"
            message = "UoM %s is not compatible with component %s." % (
                self.product_uom_id.display_name,
                component_line.component_product_id.display_name,
            )

        return {
            "component_axis_ptav_id": component_line.ptav_id.id,
            "quantity_axis_ptav_id": quantity_line.ptav_id.id,
            "component_product_id": component_line.component_product_id.id,
            "quantity": quantity_line.quantity,
            "product_uom_id": self.product_uom_id.id,
            "apply_ptav_ids": [fields.Command.set(target_ptav_ids)],
            "existing_bom_line_id": existing_line.id,
            "status": status,
            "message": message,
        }

    def _prepare_preview_error_vals(self, component_line, quantity_line, message):
        target_ptav_ids = [component_line.ptav_id.id, quantity_line.ptav_id.id]
        return {
            "component_axis_ptav_id": component_line.ptav_id.id,
            "quantity_axis_ptav_id": quantity_line.ptav_id.id,
            "component_product_id": component_line.component_product_id.id,
            "quantity": quantity_line.quantity,
            "product_uom_id": self.product_uom_id.id,
            "apply_ptav_ids": [fields.Command.set(target_ptav_ids)],
            "status": "error",
            "message": message,
        }

    def _get_preview_status(self, target_ptav_ids):
        matching_lines = self._find_matching_bom_lines(target_ptav_ids)
        if self.mode == "replace":
            matching_lines = matching_lines.filtered(
                lambda line: (
                    not (
                        line.pinout_matrix_generated
                        and line.pinout_matrix_key == self.matrix_key
                    )
                )
            )

        if not matching_lines:
            return "new", "New line.", self.env["mrp.bom.line"]
        if len(matching_lines) > 1:
            return (
                "error",
                "Multiple existing BoM lines already use these Apply on Variants.",
                self.env["mrp.bom.line"],
            )
        if self.mode == "append":
            return "skip_duplicate", "Matching BoM line already exists.", matching_lines
        if self.mode == "update":
            return "update", "Matching BoM line will be updated.", matching_lines
        return (
            "skip_duplicate",
            "Manual or other matrix line already exists.",
            matching_lines,
        )

    def _find_matching_bom_lines(self, target_ptav_ids):
        self.ensure_one()
        target_ids = set(target_ptav_ids)
        lines = self.env["mrp.bom.line"].search(
            [
                ("bom_id", "=", self.bom_id.id),
                ("display_type", "=", False),
            ]
        )
        generated_lines = lines.filtered(
            lambda line: (
                line.pinout_matrix_generated
                and line.pinout_matrix_key == self.matrix_key
                and set(line.bom_product_template_attribute_value_ids.ids) == target_ids
            )
        )
        if generated_lines:
            return generated_lines
        return lines.filtered(
            lambda line: (
                set(line.bom_product_template_attribute_value_ids.ids) == target_ids
            )
        )

    def _replace_generated_lines(self):
        self.ensure_one()
        self.env["mrp.bom.line"].search(
            [
                ("bom_id", "=", self.bom_id.id),
                ("pinout_matrix_generated", "=", True),
                ("pinout_matrix_key", "=", self.matrix_key),
            ]
        ).unlink()

    def _prepare_section_line_vals(self, ptav, sequence):
        self.ensure_one()
        return {
            "bom_id": self.bom_id.id,
            "sequence": sequence,
            "display_type": "line_section",
            "name": ptav.name,
            "product_qty": 1.0,
            "pinout_matrix_generated": True,
            "pinout_matrix_key": self.matrix_key,
            "pinout_matrix_component_axis_ptav_id": ptav.id,
        }

    def _prepare_product_line_vals(self, component_line, quantity_line, sequence):
        self.ensure_one()
        target_ptav_ids = [component_line.ptav_id.id, quantity_line.ptav_id.id]
        return {
            "bom_id": self.bom_id.id,
            "sequence": sequence,
            "product_id": component_line.component_product_id.id,
            "product_qty": quantity_line.quantity,
            "product_uom_id": self.product_uom_id.id,
            "bom_product_template_attribute_value_ids": [
                fields.Command.set(target_ptav_ids)
            ],
            "pinout_matrix_generated": True,
            "pinout_matrix_key": self.matrix_key,
            "pinout_matrix_component_axis_ptav_id": component_line.ptav_id.id,
            "pinout_matrix_quantity_axis_ptav_id": quantity_line.ptav_id.id,
        }

    def _find_preview_line(self, component_ptav, quantity_ptav):
        self.ensure_one()
        return self.preview_line_ids.filtered(
            lambda line: (
                line.component_axis_ptav_id == component_ptav
                and line.quantity_axis_ptav_id == quantity_ptav
            )
        )[:1]

    def _get_component_preview_lines(self, component_ptav):
        self.ensure_one()
        return self.preview_line_ids.filtered(
            lambda line: line.component_axis_ptav_id == component_ptav
        )

    def _find_generated_section_line(self, component_ptav):
        self.ensure_one()
        return self.env["mrp.bom.line"].search(
            [
                ("bom_id", "=", self.bom_id.id),
                ("display_type", "=", "line_section"),
                ("pinout_matrix_generated", "=", True),
                ("pinout_matrix_key", "=", self.matrix_key),
                ("pinout_matrix_component_axis_ptav_id", "=", component_ptav.id),
            ],
            limit=1,
        )

    def _ordered_component_lines(self):
        self.ensure_one()
        return self.component_mapping_line_ids.sorted(
            lambda line: (line.sequence, line.ptav_id.id)
        )

    def _ordered_quantity_lines(self):
        self.ensure_one()
        return self.quantity_mapping_line_ids.sorted(
            lambda line: (line.sequence, line.ptav_id.id)
        )

    def _make_matrix_key(self):
        self.ensure_one()
        names = [
            self.component_axis_attribute_id.name or "",
            self.quantity_axis_attribute_id.name or "",
        ]
        parts = []
        for name in names:
            slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            parts.append(slug or "axis")
        return "__".join(parts)


class MrpBomVariantMatrixComponentLine(models.TransientModel):
    _name = "mrp.bom.variant.matrix.component.line"
    _description = "BoM Variant Matrix Component Mapping"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        comodel_name="mrp.bom.variant.matrix.wizard",
        required=True,
        ondelete="cascade",
    )
    ptav_id = fields.Many2one(
        comodel_name="product.template.attribute.value",
        string="Component Axis Value",
        required=True,
    )
    attribute_id = fields.Many2one(
        related="ptav_id.attribute_id",
        string="Attribute",
        readonly=True,
    )
    name = fields.Char(
        compute="_compute_name",
        string="Name",
    )
    component_product_id = fields.Many2one(
        comodel_name="product.product",
        string="Component Product",
    )
    sequence = fields.Integer(default=10)

    @api.depends("ptav_id")
    def _compute_name(self):
        for line in self:
            line.name = line.ptav_id.name

    @api.constrains("ptav_id", "wizard_id")
    def _check_ptav_matches_wizard(self):
        for line in self:
            wizard = line.wizard_id
            if not wizard or not line.ptav_id:
                continue
            if line.ptav_id.product_tmpl_id != wizard.product_tmpl_id:
                raise ValidationError(
                    "Component axis value must belong to the BoM template."
                )
            if line.ptav_id.attribute_id != wizard.component_axis_attribute_id:
                raise ValidationError("Component axis value uses the wrong attribute.")


class MrpBomVariantMatrixQuantityLine(models.TransientModel):
    _name = "mrp.bom.variant.matrix.quantity.line"
    _description = "BoM Variant Matrix Quantity Mapping"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        comodel_name="mrp.bom.variant.matrix.wizard",
        required=True,
        ondelete="cascade",
    )
    ptav_id = fields.Many2one(
        comodel_name="product.template.attribute.value",
        string="Quantity Axis Value",
        required=True,
    )
    attribute_id = fields.Many2one(
        related="ptav_id.attribute_id",
        string="Attribute",
        readonly=True,
    )
    name = fields.Char(
        compute="_compute_name",
        string="Name",
    )
    quantity = fields.Float(
        string="Quantity",
        required=True,
    )
    sequence = fields.Integer(default=10)

    @api.depends("ptav_id")
    def _compute_name(self):
        for line in self:
            line.name = line.ptav_id.name

    @api.constrains("ptav_id", "wizard_id")
    def _check_quantity_line(self):
        for line in self:
            wizard = line.wizard_id
            if not wizard or not line.ptav_id:
                continue
            if line.ptav_id.product_tmpl_id != wizard.product_tmpl_id:
                raise ValidationError(
                    "Quantity axis value must belong to the BoM template."
                )
            if line.ptav_id.attribute_id != wizard.quantity_axis_attribute_id:
                raise ValidationError("Quantity axis value uses the wrong attribute.")


class MrpBomVariantMatrixPreviewLine(models.TransientModel):
    _name = "mrp.bom.variant.matrix.preview.line"
    _description = "BoM Variant Matrix Preview Line"

    wizard_id = fields.Many2one(
        comodel_name="mrp.bom.variant.matrix.wizard",
        required=True,
        ondelete="cascade",
    )
    component_axis_ptav_id = fields.Many2one(
        comodel_name="product.template.attribute.value",
        string="Component Axis Value",
        readonly=True,
    )
    quantity_axis_ptav_id = fields.Many2one(
        comodel_name="product.template.attribute.value",
        string="Quantity Axis Value",
        readonly=True,
    )
    component_product_id = fields.Many2one(
        comodel_name="product.product",
        string="Component Product",
        readonly=True,
    )
    quantity = fields.Float(readonly=True)
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="UoM",
        readonly=True,
    )
    apply_ptav_ids = fields.Many2many(
        comodel_name="product.template.attribute.value",
        relation="mrp_bom_var_matrix_preview_ptav_rel",
        column1="preview_id",
        column2="ptav_id",
        string="Apply on Variants",
        readonly=True,
    )
    existing_bom_line_id = fields.Many2one(
        comodel_name="mrp.bom.line",
        string="Existing BoM Line",
        readonly=True,
    )
    status = fields.Selection(
        selection=[
            ("new", "New"),
            ("update", "Update"),
            ("skip_duplicate", "Skip Duplicate"),
            ("error", "Error"),
        ],
        readonly=True,
    )
    message = fields.Char(readonly=True)
