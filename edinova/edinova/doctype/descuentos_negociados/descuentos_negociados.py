# Copyright (c) 2025, Maynor Vasquez and contributors
# For license information, please see license.txt

import json
from collections import Counter
from pathlib import Path

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class DescuentosNegociados(Document):
	pass


TEMPLATE_PATH = Path(
	frappe.get_app_path("edinova", "templates", "descuentos_negociados.json")
)


def _load_template() -> dict:
	with TEMPLATE_PATH.open(encoding="utf-8") as template_file:
		template = json.load(template_file)

	if not template.get("productos") or not template.get("sucursales"):
		frappe.throw(_("La plantilla de descuentos negociados está vacía o dañada."))

	branch_keys = {branch["key"] for branch in template["sucursales"]}
	if (
		len(branch_keys) != len(template["sucursales"])
		or template.get("sucursal_predeterminada") not in branch_keys
	):
		frappe.throw(_("La plantilla contiene sucursales duplicadas o una sucursal predeterminada inválida."))

	item_codes = set()
	for row in template["productos"]:
		if (
			not row.get("codigo")
			or row["codigo"] in item_codes
			or row.get("sucursal") not in branch_keys
		):
			frappe.throw(_("La plantilla de descuentos negociados contiene una fila inválida."))
		item_codes.add(row["codigo"])

	return template


def _base_item_code(item_code: str) -> str:
	return (item_code or "").rsplit(" - ", 1)[-1].strip()


def _resolve_item(base_code: str) -> dict:
	"""Resolve a portable item code without guessing when more than one item matches."""
	exact = frappe.db.get_value(
		"Item", {"name": base_code, "disabled": 0}, "name"
	)
	if exact:
		return {"status": "resolved", "item_code": exact, "match": "exact"}

	candidates = frappe.get_all(
		"Item",
		filters={"disabled": 0, "name": ["like", f"% - {base_code}"]},
		pluck="name",
		limit_page_length=2,
	)
	if len(candidates) == 1:
		return {
			"status": "resolved",
			"item_code": candidates[0],
			"match": "suffix",
		}
	if len(candidates) > 1:
		return {"status": "ambiguous", "item_code": None, "match": None}
	return {"status": "missing", "item_code": None, "match": None}


def _parse_configuration(configuration) -> frappe._dict:
	configuration = frappe.parse_json(configuration)
	if not isinstance(configuration, dict):
		frappe.throw(_("La configuración del asistente no es válida."))
	return frappe._dict(configuration)


def _validate_link(doctype: str, name: str, label: str) -> None:
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} no existe o no fue seleccionado: {1}").format(label, name or "—"))


def _validate_configuration(configuration: frappe._dict, template: dict) -> dict[str, str]:
	_validate_link("Company", configuration.company, _("Empresa"))
	_validate_link("Customer", configuration.customer, _("Cliente"))
	_validate_link(
		"Sales Taxes and Charges Template", configuration.tax_template, _("Impuesto")
	)

	tax_company = frappe.db.get_value(
		"Sales Taxes and Charges Template", configuration.tax_template, "company"
	)
	if tax_company and tax_company != configuration.company:
		frappe.throw(
			_("El impuesto seleccionado pertenece a la empresa {0}.").format(tax_company)
		)

	warehouses = frappe._dict(configuration.get("warehouses") or {})
	validated = {}
	for branch in template["sucursales"]:
		warehouse = warehouses.get(branch["key"])
		_validate_link("Warehouse", warehouse, _("Almacén de {0}").format(branch["label"]))
		warehouse_values = frappe.db.get_value(
			"Warehouse", warehouse, ["company", "disabled", "is_group"], as_dict=True
		)
		if warehouse_values.company != configuration.company:
			frappe.throw(
				_("El almacén {0} no pertenece a la empresa seleccionada.").format(warehouse)
			)
		if warehouse_values.disabled or warehouse_values.is_group:
			frappe.throw(_("El almacén {0} debe estar habilitado y no ser un grupo.").format(warehouse))
		validated[branch["key"]] = warehouse

	return validated


def _build_preview(configuration) -> dict:
	configuration = _parse_configuration(configuration)
	template = _load_template()
	warehouses = _validate_configuration(configuration, template)
	rows = []
	resolved_items = set()

	for template_row in template["productos"]:
		resolution = _resolve_item(template_row["codigo"])
		item_code = resolution["item_code"]
		if item_code and item_code in resolved_items:
			resolution = {"status": "ambiguous", "item_code": None, "match": None}
		else:
			resolved_items.add(item_code)

		rows.append(
			{
				**template_row,
				**resolution,
				"warehouse": warehouses[template_row["sucursal"]],
			}
		)

	status_counts = Counter(row["status"] for row in rows)
	return {
		"version": template["version"],
		"rows": rows,
		"summary": {
			"total": len(rows),
			"resolved": status_counts["resolved"],
			"missing": status_counts["missing"],
			"ambiguous": status_counts["ambiguous"],
		},
	}


def _guess_current_warehouses(template: dict, document: Document) -> dict[str, str]:
	branch_by_code = {
		row["codigo"]: row["sucursal"] for row in template["productos"]
	}
	warehouse_counts: dict[str, Counter] = {
		branch["key"]: Counter() for branch in template["sucursales"]
	}
	for row in document.get("productos") or []:
		branch = branch_by_code.get(_base_item_code(row.itemcode))
		if branch and row.almacen:
			warehouse_counts[branch][row.almacen] += 1

	return {
		branch: counts.most_common(1)[0][0]
		for branch, counts in warehouse_counts.items()
		if len(counts) == 1
	}


@frappe.whitelist()
def get_setup_context():
	frappe.only_for("System Manager")
	template = _load_template()
	document = frappe.get_single("Descuentos Negociados")
	product_counts = Counter(row["sucursal"] for row in template["productos"])

	return {
		"version": template["version"],
		"existing_rows": len(document.get("productos") or []),
		"branches": [
			{**branch, "product_count": product_counts[branch["key"]]}
			for branch in template["sucursales"]
		],
		"defaults": {
			"company": document.company,
			"customer": document.cardcode,
			"tax_template": document.iva,
			"warehouses": _guess_current_warehouses(template, document),
		},
	}


@frappe.whitelist()
def preview_setup(configuration):
	frappe.only_for("System Manager")
	return _build_preview(configuration)


@frappe.whitelist()
def apply_setup(configuration, replace_existing=0):
	frappe.only_for("System Manager")
	configuration = _parse_configuration(configuration)
	preview = _build_preview(configuration)
	unresolved = [row["codigo"] for row in preview["rows"] if row["status"] != "resolved"]
	if unresolved:
		frappe.throw(
			_("No se puede aplicar la plantilla. Artículos sin coincidencia única: {0}").format(
				", ".join(unresolved)
			)
		)

	document = frappe.get_single("Descuentos Negociados")
	if document.get("productos") and not cint(replace_existing):
		frappe.throw(
			_("Ya existen descuentos configurados. Confirme su reemplazo desde el asistente.")
		)

	document.company = configuration.company
	document.cardcode = configuration.customer
	document.iva = configuration.tax_template
	template = _load_template()
	document.almacen_defecto = frappe._dict(configuration.warehouses)[
		template["sucursal_predeterminada"]
	]
	document.set("productos", [])
	for row in preview["rows"]:
		document.append(
			"productos",
			{
				"itemcode": row["item_code"],
				"almacen": row["warehouse"],
				"descuento": row["descuento"],
				"iva": row["iva"],
			},
		)
	document.save()

	return {
		"message": _("Configuración aplicada correctamente."),
		"updated_rows": len(preview["rows"]),
	}
