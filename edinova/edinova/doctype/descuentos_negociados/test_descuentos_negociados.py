# Copyright (c) 2025, Maynor Vasquez and Contributors
# See license.txt

from unittest.mock import MagicMock, call, patch

from frappe.tests.utils import FrappeTestCase

from edinova.edinova.doctype.descuentos_negociados.descuentos_negociados import (
	_base_item_code,
	_build_preview,
	_guess_current_warehouses,
	_load_template,
	_resolve_item,
	apply_setup,
)


class TestDescuentosNegociados(FrappeTestCase):
	def test_portable_template_has_expected_products_and_branches(self):
		template = _load_template()

		self.assertEqual(template["version"], 1)
		self.assertEqual(len(template["productos"]), 55)
		self.assertEqual(
			{branch["key"] for branch in template["sucursales"]},
			{"SUCURSAL_PEDIDOS"},
		)
		self.assertEqual(
			{row["sucursal"] for row in template["productos"]},
			{"SUCURSAL_PEDIDOS"},
		)
		self.assertTrue(all(" - " not in row["codigo"] for row in template["productos"]))

	def test_extracts_base_code_from_environment_prefix(self):
		self.assertEqual(_base_item_code("YAESTA - BQ00028"), "BQ00028")
		self.assertEqual(_base_item_code("BQ00028"), "BQ00028")

	def test_does_not_guess_warehouse_when_existing_rows_disagree(self):
		template = {
			"sucursales": [
				{"key": "SUCURSAL_PEDIDOS", "label": "Sucursal para pedidos"}
			],
			"productos": [
				{"codigo": "A", "sucursal": "SUCURSAL_PEDIDOS"},
				{"codigo": "B", "sucursal": "SUCURSAL_PEDIDOS"},
			],
		}
		document = {
			"productos": [
				MagicMock(itemcode="A", almacen="Warehouse 64 - Y"),
				MagicMock(itemcode="B", almacen="Warehouse 65 - Y"),
			]
		}

		self.assertEqual(_guess_current_warehouses(template, document), {})

	@patch("frappe.get_all")
	@patch("frappe.db.get_value")
	def test_item_resolution_prefers_exact_code(self, get_value, get_all):
		get_value.return_value = "BQ00028"

		result = _resolve_item("BQ00028")

		self.assertEqual(result["item_code"], "BQ00028")
		self.assertEqual(result["match"], "exact")
		get_all.assert_not_called()

	@patch("frappe.get_all")
	@patch("frappe.db.get_value")
	def test_item_resolution_accepts_one_prefixed_code(self, get_value, get_all):
		get_value.return_value = None
		get_all.return_value = ["YAESTA - BQ00028"]

		result = _resolve_item("BQ00028")

		self.assertEqual(result["status"], "resolved")
		self.assertEqual(result["item_code"], "YAESTA - BQ00028")
		self.assertEqual(result["match"], "suffix")

	@patch("frappe.get_all")
	@patch("frappe.db.get_value")
	def test_item_resolution_rejects_ambiguous_suffix(self, get_value, get_all):
		get_value.return_value = None
		get_all.return_value = ["A - BQ00028", "B - BQ00028"]

		result = _resolve_item("BQ00028")

		self.assertEqual(result["status"], "ambiguous")
		self.assertIsNone(result["item_code"])

	@patch(
		"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados._resolve_item"
	)
	@patch(
		"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados._validate_configuration"
	)
	@patch(
		"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados._load_template"
	)
	def test_preview_counts_unresolved_items(
		self, load_template, validate_configuration, resolve_item
	):
		load_template.return_value = {
			"version": 1,
			"sucursales": [
				{"key": "SUCURSAL_PEDIDOS", "label": "Sucursal para pedidos"}
			],
			"productos": [
				{
					"codigo": "A",
					"sucursal": "SUCURSAL_PEDIDOS",
					"descuento": 1,
					"iva": 12,
				},
				{
					"codigo": "B",
					"sucursal": "SUCURSAL_PEDIDOS",
					"descuento": 2,
					"iva": 12,
				},
			],
		}
		validate_configuration.return_value = {
			"SUCURSAL_PEDIDOS": "Warehouse - X"
		}
		resolve_item.side_effect = [
			{"status": "resolved", "item_code": "A", "match": "exact"},
			{"status": "missing", "item_code": None, "match": None},
		]

		preview = _build_preview({})

		self.assertEqual(preview["summary"]["total"], 2)
		self.assertEqual(preview["summary"]["resolved"], 1)
		self.assertEqual(preview["summary"]["missing"], 1)

	@patch(
		"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados.frappe.only_for"
	)
	@patch(
		"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados._build_preview"
	)
	@patch(
		"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados._load_template"
	)
	@patch(
		"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados.frappe.get_single"
	)
	def test_apply_maps_environment_links_without_changing_template_values(
		self, get_single, load_template, build_preview, only_for
	):
		document = MagicMock()
		document.get.return_value = []
		get_single.return_value = document
		load_template.return_value = {
			"sucursal_predeterminada": "SUCURSAL_PEDIDOS"
		}
		build_preview.return_value = {
			"rows": [
				{
					"codigo": "BQ00028",
					"status": "resolved",
					"item_code": "YAESTA - BQ00028",
					"warehouse": "Warehouse 65 - Y",
					"descuento": 4.25,
					"iva": 12,
				}
			]
		}
		configuration = {
			"company": "Company Y",
			"customer": "Customer W",
			"tax_template": "Tax Y",
			"warehouses": {
				"SUCURSAL_PEDIDOS": "Warehouse 65 - Y",
			},
		}

		result = apply_setup(configuration)

		only_for.assert_called_once_with("System Manager")
		self.assertEqual(document.company, "Company Y")
		self.assertEqual(document.cardcode, "Customer W")
		self.assertEqual(document.iva, "Tax Y")
		self.assertEqual(document.almacen_defecto, "Warehouse 65 - Y")
		document.set.assert_called_once_with("productos", [])
		document.append.assert_has_calls(
			[
				call(
					"productos",
					{
						"itemcode": "YAESTA - BQ00028",
						"almacen": "Warehouse 65 - Y",
						"descuento": 4.25,
						"iva": 12,
					},
				)
			]
		)
		document.save.assert_called_once_with()
		self.assertEqual(result["updated_rows"], 1)
