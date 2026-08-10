// Copyright (c) 2025, Maynor Vasquez and contributors
// For license information, please see license.txt

const SETUP_METHOD =
	"edinova.edinova.doctype.descuentos_negociados.descuentos_negociados";

frappe.ui.form.on("Descuentos Negociados", {
	refresh(frm) {
		if (!frappe.user.has_role("System Manager")) {
			return;
		}

		frm.add_custom_button(__("Asistente de configuración"), () => {
			open_setup_assistant(frm);
		});
	},
});

async function open_setup_assistant(frm) {
	const response = await frappe.call({
		method: `${SETUP_METHOD}.get_setup_context`,
		freeze: true,
		freeze_message: __("Cargando plantilla de descuentos..."),
	});
	const context = response.message;
	let dialog;
	let preview_signature = null;

	const branch_fields = context.branches.flatMap((branch, index) => {
		const fields = [];
		if (index > 0 && index % 2 === 1) {
			fields.push({ fieldtype: "Column Break" });
		}
		fields.push({
			fieldtype: "Link",
			fieldname: branch_fieldname(branch.key),
			label: __("Almacén {0} ({1} productos)", [
				branch.label,
				branch.product_count,
			]),
			options: "Warehouse",
			reqd: 1,
			default: context.defaults.warehouses[branch.key],
		});
		return fields;
	});

	dialog = new frappe.ui.Dialog({
		title: __("Configurar descuentos desde plantilla"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"Seleccione los registros de este sitio. La vista previa no realiza cambios."
				)}</p>`,
			},
			{
				fieldtype: "Link",
				fieldname: "company",
				label: __("Empresa"),
				options: "Company",
				reqd: 1,
				default: context.defaults.company,
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Link",
				fieldname: "customer",
				label: __("Cliente"),
				options: "Customer",
				reqd: 1,
				default: context.defaults.customer,
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Link",
				fieldname: "tax_template",
				label: __("Impuesto predeterminado"),
				options: "Sales Taxes and Charges Template",
				reqd: 1,
				default: context.defaults.tax_template,
			},
			{
				fieldtype: "Section Break",
				label: __("Asignación de sucursales"),
			},
			...branch_fields,
			{ fieldtype: "Section Break" },
			{ fieldtype: "HTML", fieldname: "preview_result" },
		],
		primary_action_label: __("Revisar coincidencias"),
		primary_action: () => preview_configuration(),
	});

	dialog.fields_dict.tax_template.get_query = () => ({
		filters: { company: dialog.get_value("company") },
	});
	for (const branch of context.branches) {
		dialog.fields_dict[branch_fieldname(branch.key)].get_query = () => ({
			filters: {
				company: dialog.get_value("company"),
				disabled: 0,
				is_group: 0,
			},
		});
	}

	dialog.show();

	function get_configuration() {
		const values = dialog.get_values();
		if (!values) {
			return null;
		}
		const warehouses = {};
		for (const branch of context.branches) {
			warehouses[branch.key] = values[branch_fieldname(branch.key)];
		}
		return {
			company: values.company,
			customer: values.customer,
			tax_template: values.tax_template,
			warehouses,
		};
	}

	async function preview_configuration() {
		const configuration = get_configuration();
		if (!configuration) {
			return;
		}

		const result = await frappe.call({
			method: `${SETUP_METHOD}.preview_setup`,
			args: { configuration: JSON.stringify(configuration) },
			freeze: true,
			freeze_message: __("Buscando artículos..."),
		});
		render_preview(dialog, result.message);

		if (
			result.message.summary.missing === 0 &&
			result.message.summary.ambiguous === 0
		) {
			preview_signature = JSON.stringify(configuration);
			dialog.set_primary_action(__("Aplicar plantilla"), apply_configuration);
		} else {
			preview_signature = null;
			dialog.set_primary_action(
				__("Revisar coincidencias"),
				preview_configuration
			);
		}
	}

	function apply_configuration() {
		const configuration = get_configuration();
		if (!configuration) {
			return;
		}
		if (JSON.stringify(configuration) !== preview_signature) {
			preview_configuration();
			return;
		}

		const replacement_warning = context.existing_rows
			? __(
					"Se reemplazarán las {0} filas configuradas actualmente por la versión {1} de la plantilla.",
					[context.existing_rows, context.version]
			  )
			: __("Se crearán los descuentos de la versión {0} de la plantilla.", [
					context.version,
			  ]);
		frappe.confirm(replacement_warning, async () => {
			const result = await frappe.call({
				method: `${SETUP_METHOD}.apply_setup`,
				args: {
					configuration: JSON.stringify(configuration),
					replace_existing: context.existing_rows ? 1 : 0,
				},
				freeze: true,
				freeze_message: __("Aplicando configuración..."),
			});
			dialog.hide();
			await frm.reload_doc();
			frappe.show_alert({ message: result.message.message, indicator: "green" });
		});
	}
}

function branch_fieldname(branch_key) {
	return `warehouse_${branch_key.toLowerCase()}`;
}

function render_preview(dialog, preview) {
	const escape = frappe.utils.escape_html;
	const unresolved = preview.rows.filter((row) => row.status !== "resolved");
	let details = `<div class="alert alert-success">${__(
		"Los {0} artículos tienen una coincidencia única.",
		[preview.summary.resolved]
	)}</div>`;

	if (unresolved.length) {
		const rows = unresolved
			.map(
				(row) => `<tr>
					<td>${escape(row.codigo)}</td>
					<td>${
						row.status === "ambiguous"
							? __("Más de una coincidencia")
							: __("No encontrado")
					}</td>
				</tr>`
			)
			.join("");
		details = `<div class="alert alert-danger">${__(
			"No se aplicará la plantilla hasta resolver {0} artículos.",
			[unresolved.length]
		)}</div>
		<table class="table table-bordered table-sm">
			<thead><tr><th>${__("Código base")}</th><th>${__("Resultado")}</th></tr></thead>
			<tbody>${rows}</tbody>
		</table>`;
	}

	dialog.fields_dict.preview_result.$wrapper.html(`
		<div class="mt-3">
			<p><strong>${__("Vista previa de plantilla v{0}", [preview.version])}</strong></p>
			<p>${__("Total: {0} · Encontrados: {1} · Faltantes: {2} · Ambiguos: {3}", [
				preview.summary.total,
				preview.summary.resolved,
				preview.summary.missing,
				preview.summary.ambiguous,
			])}</p>
			${details}
		</div>
	`);
}
