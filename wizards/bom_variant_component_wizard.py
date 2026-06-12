import itertools
import re

from odoo import api, fields, models
from odoo.exceptions import UserError


class MrpBomVariantComponentWizard(models.TransientModel):
    _name = "mrp.bom.variant.component.wizard"
    _description = "BoM Variant Component Generator"

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
        default="variant_components",
        required=True,
    )
    create_sections = fields.Boolean(
        string="Create Sections",
        default=True,
    )
    missing_variant_policy = fields.Selection(
        selection=[
            ("error", "Block apply"),
            ("skip", "Skip missing variants"),
        ],
        string="Missing Component Variants",
        default="error",
        required=True,
    )
    sequence_start = fields.Integer(
        string="Sequence Start",
        default=2000,
        required=True,
    )
    sequence_step = fields.Integer(
        string="Sequence Step",
        default=10,
        required=True,
    )
    rule_line_ids = fields.One2many(
        comodel_name="mrp.bom.variant.component.rule.line",
        inverse_name="wizard_id",
        string="Component Rules",
    )
    preview_line_ids = fields.One2many(
        comodel_name="mrp.bom.variant.component.preview.line",
        inverse_name="wizard_id",
        string="Preview",
        readonly=True,
    )

    def action_preview(self):
        self.ensure_one()
        self._rebuild_preview()
        return self._reload_action()

    def action_apply_components(self):
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

        for rule in self._ordered_rules():
            rule_preview_lines = self.preview_line_ids.filtered(
                lambda line: line.rule_id == rule and line.status in ("new", "update")
            )
            if not rule_preview_lines:
                continue

            if self.create_sections:
                section_vals = self._prepare_section_line_vals(rule, sequence)
                section_line = self._find_generated_section_line(rule)
                if section_line:
                    section_line.write(section_vals)
                else:
                    self.env["mrp.bom.line"].create(section_vals)
                sequence += self.sequence_step

            for preview_line in rule_preview_lines.sorted(
                lambda line: line.apply_values_display or ""
            ):
                vals = self._prepare_product_line_vals(preview_line, sequence)
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
            "name": "Generate Variant Components",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _validate_configuration(self):
        self.ensure_one()
        if not self.bom_id:
            raise UserError("Select a BoM first.")
        if not self.product_tmpl_id:
            raise UserError("The selected BoM has no product template.")
        if self.bom_id.product_id:
            raise UserError(
                "Variant component generation is intended for template BoMs. "
                "The selected BoM is restricted to a single product variant."
            )
        if not self.matrix_key:
            raise UserError("Matrix key is required.")
        if self.sequence_step <= 0:
            raise UserError("Sequence step must be greater than zero.")
        if not self.rule_line_ids:
            raise UserError("Add at least one component rule.")
        rule_keys = [rule.rule_key for rule in self.rule_line_ids if rule.rule_key]
        if len(rule_keys) != len(self.rule_line_ids):
            raise UserError("Every component rule must have a rule key.")
        if len(rule_keys) != len(set(rule_keys)):
            raise UserError("Component rule keys must be unique.")

    def _rebuild_preview(self):
        self.ensure_one()
        self._validate_configuration()
        self.preview_line_ids.unlink()

        commands = []
        seen_keys = set()
        for rule in self._ordered_rules():
            for vals in self._iter_rule_preview_vals(rule):
                apply_commands = vals.get("apply_ptav_ids") or []
                apply_ids = (
                    tuple(apply_commands[0][2])
                    if apply_commands
                    else ("rule_error", rule.id)
                )
                dedupe_key = (rule.rule_key, apply_ids)
                if dedupe_key in seen_keys:
                    vals.update(
                        {
                            "status": "error",
                            "message": "Duplicate preview row for the same rule and Apply on Variants.",
                        }
                    )
                seen_keys.add(dedupe_key)
                commands.append(fields.Command.create(vals))
        self.preview_line_ids = commands

    def _iter_rule_preview_vals(self, rule):
        mapping_pairs, errors = self._get_rule_mapping_pairs(rule)
        if errors:
            yield self._prepare_rule_error_preview_vals(rule, "; ".join(errors))
            return

        parent_ptav_groups = [
            self._get_parent_axis_ptavs(parent_attribute)
            for parent_attribute, _component_attribute in mapping_pairs
        ]
        empty_axes = [
            parent_attribute.display_name
            for (parent_attribute, _component_attribute), ptavs in zip(
                mapping_pairs, parent_ptav_groups
            )
            if not ptavs
        ]
        if empty_axes:
            yield self._prepare_rule_error_preview_vals(
                rule,
                "Parent template has no values for: %s." % ", ".join(empty_axes),
            )
            return

        for parent_ptavs in itertools.product(*parent_ptav_groups):
            yield self._prepare_preview_line_vals(rule, mapping_pairs, parent_ptavs)

    def _get_rule_mapping_pairs(self, rule):
        pairs = []
        errors = []
        raw_pairs = [
            (rule.parent_attribute_1_id, rule.component_attribute_1_id),
            (rule.parent_attribute_2_id, rule.component_attribute_2_id),
            (rule.parent_attribute_3_id, rule.component_attribute_3_id),
        ]
        for index, (parent_attribute, component_attribute) in enumerate(
            raw_pairs, start=1
        ):
            if not parent_attribute and not component_attribute:
                continue
            if not parent_attribute or not component_attribute:
                errors.append("Mapping %s must have both attributes." % index)
                continue
            pairs.append((parent_attribute, component_attribute))

        if not rule.component_tmpl_id:
            errors.append("Component template is required.")
        if not rule.quantity or rule.quantity <= 0:
            errors.append("Quantity must be greater than zero.")
        if not rule.product_uom_id:
            errors.append("UoM is required.")
        if not pairs:
            errors.append("Add at least one parent/component attribute mapping.")

        for parent_attribute, component_attribute in pairs:
            if not self._get_parent_axis_ptavs(parent_attribute):
                errors.append(
                    "Parent template does not contain attribute %s."
                    % parent_attribute.display_name
                )
            if not self._get_component_axis_ptavs(rule, component_attribute):
                errors.append(
                    "Component template %s does not contain attribute %s."
                    % (
                        rule.component_tmpl_id.display_name,
                        component_attribute.display_name,
                    )
                )

        parent_attribute_ids = [parent_attribute.id for parent_attribute, _ in pairs]
        if len(parent_attribute_ids) != len(set(parent_attribute_ids)):
            errors.append("Parent attributes must be unique inside a rule.")

        component_attribute_ids = [
            component_attribute.id for _parent_attribute, component_attribute in pairs
        ]
        if len(component_attribute_ids) != len(set(component_attribute_ids)):
            errors.append("Component attributes must be unique inside a rule.")

        return pairs, errors

    def _prepare_preview_line_vals(self, rule, mapping_pairs, parent_ptavs):
        apply_ptav_ids = [ptav.id for ptav in parent_ptavs]
        component_ptavs = self.env["product.template.attribute.value"]
        messages = []

        for parent_ptav, (_parent_attribute, component_attribute) in zip(
            parent_ptavs, mapping_pairs
        ):
            component_ptav = self._find_component_ptav(
                rule, component_attribute, parent_ptav
            )
            if component_ptav:
                component_ptavs |= component_ptav
            else:
                messages.append(
                    "No component value for %s on %s."
                    % (parent_ptav.display_name, component_attribute.display_name)
                )

        component_product = self.env["product.product"]
        status = False
        status_message = False
        if not messages:
            (
                component_product,
                product_message,
                product_error_code,
            ) = self._find_component_product(rule, component_ptavs)
            if product_message:
                if (
                    product_error_code == "missing"
                    and self.missing_variant_policy == "skip"
                ):
                    status = "skip_missing"
                    status_message = product_message
                else:
                    messages.append(product_message)

        if component_product:
            status, status_message, existing_line = self._get_preview_status(
                rule, apply_ptav_ids, component_product
            )
        else:
            existing_line = self.env["mrp.bom.line"]
            status = status or "error"
            status_message = status_message or "Component variant was not resolved."

        if messages:
            status = "error"
            status_message = "; ".join(messages)
        elif component_product and (
            rule.product_uom_id.category_id != component_product.uom_id.category_id
        ):
            status = "error"
            status_message = "UoM %s is not compatible with component %s." % (
                rule.product_uom_id.display_name,
                component_product.display_name,
            )

        return {
            "rule_id": rule.id,
            "component_tmpl_id": rule.component_tmpl_id.id,
            "component_product_id": component_product.id,
            "quantity": rule.quantity,
            "product_uom_id": rule.product_uom_id.id,
            "apply_ptav_ids": [fields.Command.set(apply_ptav_ids)],
            "apply_values_display": ", ".join(
                ptav.display_name for ptav in parent_ptavs
            ),
            "existing_bom_line_id": existing_line.id,
            "status": status,
            "message": status_message,
        }

    def _prepare_rule_error_preview_vals(self, rule, message):
        return {
            "rule_id": rule.id,
            "component_tmpl_id": rule.component_tmpl_id.id,
            "quantity": rule.quantity,
            "product_uom_id": rule.product_uom_id.id,
            "status": "error",
            "message": message,
        }

    def _get_preview_status(self, rule, apply_ptav_ids, component_product):
        matching_lines = self._find_matching_bom_lines(
            rule, apply_ptav_ids, component_product
        )
        if self.mode == "replace":
            matching_lines = matching_lines.filtered(
                lambda line: (
                    not (
                        line.pinout_matrix_generated
                        and line.pinout_matrix_key == self.matrix_key
                        and line.pinout_matrix_rule_key == rule.rule_key
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
            "Manual or other generated line already exists.",
            matching_lines,
        )

    def _find_matching_bom_lines(self, rule, apply_ptav_ids, component_product):
        target_ids = set(apply_ptav_ids)
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
                and line.pinout_matrix_rule_key == rule.rule_key
                and set(line.bom_product_template_attribute_value_ids.ids) == target_ids
            )
        )
        if generated_lines:
            return generated_lines
        if component_product:
            lines = lines.filtered(lambda line: line.product_id == component_product)
        return lines.filtered(
            lambda line: (
                set(line.bom_product_template_attribute_value_ids.ids) == target_ids
            )
        )

    def _replace_generated_lines(self):
        self.env["mrp.bom.line"].search(
            [
                ("bom_id", "=", self.bom_id.id),
                ("pinout_matrix_generated", "=", True),
                ("pinout_matrix_key", "=", self.matrix_key),
            ]
        ).unlink()

    def _prepare_section_line_vals(self, rule, sequence):
        return {
            "bom_id": self.bom_id.id,
            "sequence": sequence,
            "display_type": "line_section",
            "name": rule.component_tmpl_id.display_name,
            "product_id": False,
            "product_qty": 1.0,
            "product_uom_id": False,
            "pinout_matrix_generated": True,
            "pinout_matrix_key": self.matrix_key,
            "pinout_matrix_rule_key": rule.rule_key,
            "pinout_matrix_component_tmpl_id": rule.component_tmpl_id.id,
            "pinout_matrix_component_axis_ptav_id": False,
            "pinout_matrix_quantity_axis_ptav_id": False,
        }

    def _prepare_product_line_vals(self, preview_line, sequence):
        return {
            "bom_id": self.bom_id.id,
            "sequence": sequence,
            "product_id": preview_line.component_product_id.id,
            "product_qty": preview_line.quantity,
            "product_uom_id": preview_line.product_uom_id.id,
            "bom_product_template_attribute_value_ids": [
                fields.Command.set(preview_line.apply_ptav_ids.ids)
            ],
            "pinout_matrix_generated": True,
            "pinout_matrix_key": self.matrix_key,
            "pinout_matrix_rule_key": preview_line.rule_id.rule_key,
            "pinout_matrix_component_tmpl_id": preview_line.component_tmpl_id.id,
            "pinout_matrix_component_axis_ptav_id": False,
            "pinout_matrix_quantity_axis_ptav_id": False,
        }

    def _find_generated_section_line(self, rule):
        return self.env["mrp.bom.line"].search(
            [
                ("bom_id", "=", self.bom_id.id),
                ("display_type", "=", "line_section"),
                ("pinout_matrix_generated", "=", True),
                ("pinout_matrix_key", "=", self.matrix_key),
                ("pinout_matrix_rule_key", "=", rule.rule_key),
            ],
            limit=1,
        )

    def _find_component_ptav(self, rule, component_attribute, parent_ptav):
        candidates = self._get_component_axis_ptavs(rule, component_attribute)
        exact = candidates.filtered(
            lambda ptav: (
                ptav.product_attribute_value_id
                == parent_ptav.product_attribute_value_id
            )
        )
        if exact:
            return exact[:1]
        by_name = candidates.filtered(lambda ptav: ptav.name == parent_ptav.name)
        return by_name[:1]

    def _find_component_product(self, rule, component_ptavs):
        component_ptav_ids = set(component_ptavs.ids)
        variants = self.env["product.product"].search(
            [("product_tmpl_id", "=", rule.component_tmpl_id.id)]
        )
        matching_variants = variants.filtered(
            lambda product: component_ptav_ids.issubset(
                set(product.product_template_attribute_value_ids.ids)
            )
        )
        if not matching_variants:
            return (
                self.env["product.product"],
                "No component variant found for %s."
                % ", ".join(component_ptavs.mapped("display_name")),
                "missing",
            )
        if len(matching_variants) > 1:
            return (
                self.env["product.product"],
                "Multiple component variants match %s."
                % ", ".join(component_ptavs.mapped("display_name")),
                "multiple",
            )
        return matching_variants, False, False

    def _get_parent_axis_ptavs(self, attribute):
        if not self.product_tmpl_id or not attribute:
            return self.env["product.template.attribute.value"]
        return self.env["product.template.attribute.value"].search(
            [
                ("product_tmpl_id", "=", self.product_tmpl_id.id),
                ("attribute_id", "=", attribute.id),
            ]
        )

    def _get_component_axis_ptavs(self, rule, attribute):
        if not rule.component_tmpl_id or not attribute:
            return self.env["product.template.attribute.value"]
        return self.env["product.template.attribute.value"].search(
            [
                ("product_tmpl_id", "=", rule.component_tmpl_id.id),
                ("attribute_id", "=", attribute.id),
            ]
        )

    def _ordered_rules(self):
        return self.rule_line_ids.sorted(lambda line: (line.sequence, line.id))


class MrpBomVariantComponentRuleLine(models.TransientModel):
    _name = "mrp.bom.variant.component.rule.line"
    _description = "BoM Variant Component Rule"
    _order = "sequence, id"
    _rec_name = "name"

    wizard_id = fields.Many2one(
        comodel_name="mrp.bom.variant.component.wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    component_tmpl_id = fields.Many2one(
        comodel_name="product.template",
        string="Component Template",
        required=True,
    )
    name = fields.Char(
        compute="_compute_name",
        string="Name",
    )
    rule_key = fields.Char(
        string="Rule Key",
        required=True,
        default="component",
    )
    quantity = fields.Float(
        string="Quantity",
        digits=(16, 6),
        default=1.0,
        required=True,
    )
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="UoM",
        required=True,
    )
    parent_attribute_1_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Parent Attribute 1",
    )
    component_attribute_1_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Component Attribute 1",
    )
    parent_attribute_2_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Parent Attribute 2",
    )
    component_attribute_2_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Component Attribute 2",
    )
    parent_attribute_3_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Parent Attribute 3",
    )
    component_attribute_3_id = fields.Many2one(
        comodel_name="product.attribute",
        string="Component Attribute 3",
    )

    @api.onchange("component_tmpl_id")
    def _onchange_component_tmpl_id(self):
        for line in self:
            if line.component_tmpl_id:
                line.product_uom_id = line.component_tmpl_id.uom_id
                if not line.rule_key or line.rule_key == "component":
                    line.rule_key = line._make_rule_key()

    @api.depends("component_tmpl_id", "rule_key")
    def _compute_name(self):
        for line in self:
            line.name = line.component_tmpl_id.display_name or line.rule_key

    def _make_rule_key(self):
        self.ensure_one()
        name = self.component_tmpl_id.default_code or self.component_tmpl_id.name or ""
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        return slug or "component"


class MrpBomVariantComponentPreviewLine(models.TransientModel):
    _name = "mrp.bom.variant.component.preview.line"
    _description = "BoM Variant Component Preview Line"

    wizard_id = fields.Many2one(
        comodel_name="mrp.bom.variant.component.wizard",
        required=True,
        ondelete="cascade",
    )
    rule_id = fields.Many2one(
        comodel_name="mrp.bom.variant.component.rule.line",
        string="Rule",
        readonly=True,
    )
    component_tmpl_id = fields.Many2one(
        comodel_name="product.template",
        string="Component Template",
        readonly=True,
    )
    component_product_id = fields.Many2one(
        comodel_name="product.product",
        string="Component Variant",
        readonly=True,
    )
    quantity = fields.Float(
        digits=(16, 6),
        readonly=True,
    )
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="UoM",
        readonly=True,
    )
    apply_ptav_ids = fields.Many2many(
        comodel_name="product.template.attribute.value",
        relation="mrp_bom_var_comp_preview_ptav_rel",
        column1="preview_id",
        column2="ptav_id",
        string="Apply on Variants",
        readonly=True,
    )
    apply_values_display = fields.Char(
        string="Apply Values",
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
            ("skip_missing", "Skip Missing"),
            ("error", "Error"),
        ],
        readonly=True,
    )
    message = fields.Char(readonly=True)
