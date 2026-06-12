from odoo import fields, models


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    def action_open_variant_matrix_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Generate Variant Matrix",
            "res_model": "mrp.bom.variant.matrix.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_bom_id": self.id,
            },
        }


class MrpBomLine(models.Model):
    _inherit = "mrp.bom.line"

    pinout_matrix_generated = fields.Boolean(
        string="Pinout Matrix Generated",
        default=False,
        copy=False,
    )
    pinout_matrix_key = fields.Char(
        string="Pinout Matrix Key",
        copy=False,
    )
    pinout_matrix_component_axis_ptav_id = fields.Many2one(
        comodel_name="product.template.attribute.value",
        string="Pinout Matrix Component Axis Value",
        copy=False,
    )
    pinout_matrix_quantity_axis_ptav_id = fields.Many2one(
        comodel_name="product.template.attribute.value",
        string="Pinout Matrix Quantity Axis Value",
        copy=False,
    )
