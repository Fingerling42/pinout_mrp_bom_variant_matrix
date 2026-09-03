from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBomVariantMatrix(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.component_attribute = cls.env["product.attribute"].create(
            {"name": "Matrix Plastic Type"}
        )
        cls.green_value, cls.black_value = cls.env[
            "product.attribute.value"
        ].create(
            [
                {
                    "name": "Matrix Green",
                    "attribute_id": cls.component_attribute.id,
                },
                {
                    "name": "Matrix Black",
                    "attribute_id": cls.component_attribute.id,
                },
            ]
        )
        cls.quantity_attribute = cls.env["product.attribute"].create(
            {"name": "Matrix Size"}
        )
        cls.small_value, cls.large_value = cls.env[
            "product.attribute.value"
        ].create(
            [
                {
                    "name": "Matrix Small",
                    "attribute_id": cls.quantity_attribute.id,
                },
                {
                    "name": "Matrix Large",
                    "attribute_id": cls.quantity_attribute.id,
                },
            ]
        )
        cls.finished_template = cls.env["product.template"].create(
            {
                "name": "Matrix Finished Product",
                "attribute_line_ids": [
                    Command.create(
                        {
                            "attribute_id": cls.component_attribute.id,
                            "value_ids": [
                                Command.set([cls.green_value.id, cls.black_value.id])
                            ],
                        }
                    ),
                    Command.create(
                        {
                            "attribute_id": cls.quantity_attribute.id,
                            "value_ids": [
                                Command.set([cls.small_value.id, cls.large_value.id])
                            ],
                        }
                    ),
                ],
            }
        )
        cls.bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.finished_template.id,
                "product_qty": 1.0,
                "product_uom_id": cls.finished_template.uom_id.id,
                "type": "normal",
            }
        )
        cls.green_component = cls.env["product.product"].create(
            {"name": "Matrix Standalone Green Component"}
        )
        cls.black_component = cls.env["product.product"].create(
            {"name": "Matrix Standalone Black Component"}
        )
        cls.component_ptavs = cls.env[
            "product.template.attribute.value"
        ].search(
            [
                ("product_tmpl_id", "=", cls.finished_template.id),
                ("attribute_id", "=", cls.component_attribute.id),
            ]
        )
        cls.quantity_ptavs = cls.env[
            "product.template.attribute.value"
        ].search(
            [
                ("product_tmpl_id", "=", cls.finished_template.id),
                ("attribute_id", "=", cls.quantity_attribute.id),
            ]
        )

    def _component_mapping_commands(self):
        products_by_value = {
            self.green_value.id: self.green_component,
            self.black_value.id: self.black_component,
        }
        return [
            Command.create(
                {
                    "ptav_id": ptav.id,
                    "component_product_id": products_by_value[
                        ptav.product_attribute_value_id.id
                    ].id,
                    "sequence": sequence,
                }
            )
            for sequence, ptav in enumerate(self.component_ptavs, start=1)
        ]

    def test_generate_standalone_components_without_quantity_axis(self):
        wizard = self.env["mrp.bom.variant.matrix.wizard"].create(
            {
                "bom_id": self.bom.id,
                "component_axis_attribute_id": self.component_attribute.id,
                "default_quantity": 2.5,
                "product_uom_id": self.finished_template.uom_id.id,
                "create_sections": False,
                "matrix_key": "single_axis_test",
                "component_mapping_line_ids": self._component_mapping_commands(),
            }
        )

        wizard.action_apply_matrix()

        generated_lines = self.bom.bom_line_ids.filtered(
            lambda line: line.pinout_matrix_key == "single_axis_test"
        )
        self.assertEqual(len(generated_lines), 2)
        for line in generated_lines:
            self.assertEqual(line.product_qty, 2.5)
            self.assertFalse(line.pinout_matrix_quantity_axis_ptav_id)
            self.assertEqual(
                line.bom_product_template_attribute_value_ids,
                line.pinout_matrix_component_axis_ptav_id,
            )

        products_by_apply_value = {}
        for line in generated_lines:
            apply_value = (
                line.bom_product_template_attribute_value_ids
                .product_attribute_value_id
            )
            products_by_apply_value[apply_value.id] = line.product_id
        self.assertEqual(
            products_by_apply_value[self.green_value.id], self.green_component
        )
        self.assertEqual(
            products_by_apply_value[self.black_value.id], self.black_component
        )
        for variant in self.finished_template.product_variant_ids:
            applicable_lines = generated_lines.filtered(
                lambda line: not line._skip_bom_line(variant)
            )
            component_value = variant.product_template_attribute_value_ids.filtered(
                lambda ptav: ptav.attribute_id == self.component_attribute
            ).product_attribute_value_id
            self.assertEqual(len(applicable_lines), 1)
            self.assertEqual(
                applicable_lines.product_id,
                products_by_apply_value[component_value.id],
            )

    def test_existing_two_axis_generation_is_preserved(self):
        quantities_by_value = {
            self.small_value.id: 1.25,
            self.large_value.id: 3.5,
        }
        quantity_commands = [
            Command.create(
                {
                    "ptav_id": ptav.id,
                    "quantity": quantities_by_value[
                        ptav.product_attribute_value_id.id
                    ],
                    "sequence": sequence,
                }
            )
            for sequence, ptav in enumerate(self.quantity_ptavs, start=1)
        ]
        wizard = self.env["mrp.bom.variant.matrix.wizard"].create(
            {
                "bom_id": self.bom.id,
                "component_axis_attribute_id": self.component_attribute.id,
                "quantity_axis_attribute_id": self.quantity_attribute.id,
                "product_uom_id": self.finished_template.uom_id.id,
                "create_sections": False,
                "matrix_key": "two_axis_test",
                "component_mapping_line_ids": self._component_mapping_commands(),
                "quantity_mapping_line_ids": quantity_commands,
            }
        )

        wizard.action_apply_matrix()

        generated_lines = self.bom.bom_line_ids.filtered(
            lambda line: line.pinout_matrix_key == "two_axis_test"
        )
        self.assertEqual(len(generated_lines), 4)
        for line in generated_lines:
            self.assertEqual(
                len(line.bom_product_template_attribute_value_ids), 2
            )
            quantity_value = (
                line.pinout_matrix_quantity_axis_ptav_id
                .product_attribute_value_id
            )
            self.assertEqual(
                line.product_qty,
                quantities_by_value[quantity_value.id],
            )
