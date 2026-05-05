let sync_dialog = null;
let sync_poll_timer = null;

frappe.ui.form.on("Edinova Sincronizar Ordenes de Venta", {
    refresh(frm) {
        render_detalle(frm);
        bind_realtime(frm);

        if (frm.doc.detalle) {
            frm.add_custom_button(__("Exportar CSV"), () => exportar_csv(frm));
        }

        if (frm.doc.payload_original) {
            frm.add_custom_button(__("Ver payload original"), () => mostrar_payload(frm));
        }

        if (!frm.is_new() && frm.doc.status === "En proceso") {
            mostrar_modal_sync(frm);
        } else {
            cerrar_modal_sync();
        }
    },
});

function bind_realtime(frm) {
    frappe.realtime.off("edinova_sync_done");
    frappe.realtime.on("edinova_sync_done", (data) => {
        if (data && data.name === frm.doc.name) {
            cerrar_modal_sync();
            frm.reload_doc();
        }
    });
}

function mostrar_modal_sync(frm) {
    if (sync_dialog) return;

    // Fallback: si el evento realtime se pierde (socket caído, reconexión, etc.),
    // recargamos el doc cada 5s; cuando el status cambie, refresh cierra el modal.
    if (sync_poll_timer) clearInterval(sync_poll_timer);
    sync_poll_timer = setInterval(() => {
        if (frm && frm.doc && frm.doc.status === "En proceso") {
            frm.reload_doc();
        }
    }, 5000);

    sync_dialog = new frappe.ui.Dialog({
        title: __("Sincronizando con Walmart"),
        static: true,
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "msg",
                options: `
                    <div style="text-align:center;padding:24px 12px;">
                        <div class="spinner-border text-primary" role="status"
                             style="width:3rem;height:3rem;border-width:.3em;"></div>
                        <p style="margin-top:18px;font-size:14px;font-weight:500;">
                            ${__("Descargando órdenes desde Walmart…")}
                        </p>
                        <p style="color:#888;font-size:12px;margin-bottom:0;">
                            ${__("Por favor no cierres esta pestaña. El proceso tarda solo unos segundos y se cerrará automáticamente al terminar.")}
                        </p>
                    </div>
                `,
            },
        ],
        secondary_action_label: __("Cerrar de todos modos"),
        secondary_action: () => cerrar_modal_sync(),
    });
    sync_dialog.show();
    sync_dialog.get_close_btn().hide();
}

function cerrar_modal_sync() {
    if (sync_poll_timer) {
        clearInterval(sync_poll_timer);
        sync_poll_timer = null;
    }
    if (sync_dialog) {
        sync_dialog.hide();
        sync_dialog = null;
    }
}

function parse_detalle(frm) {
    if (!frm.doc.detalle) return null;
    try {
        return JSON.parse(frm.doc.detalle);
    } catch (e) {
        console.error("Error parseando detalle:", e);
        return null;
    }
}

function render_detalle(frm) {
    const $wrapper = frm.fields_dict.detalle.$wrapper;
    $wrapper.find(".detalle-table").remove();

    const data = parse_detalle(frm);
    if (!data) return;

    const validacion = data.validacion || [];

    const $container = $('<div class="detalle-table"></div>').appendTo($wrapper);

    render_resumen($container, validacion);
    render_buscador($container);
    render_tablas($container, validacion);
}

function render_resumen($container, validacion) {
    let total_pos = validacion.length;
    let total_items = 0;
    let items_ok = 0;
    let items_diff = 0;

    validacion.forEach((order) => {
        (order.items || []).forEach((item) => {
            total_items += 1;
            if (item.match) items_ok += 1;
            else items_diff += 1;
        });
    });

    const $resumen = $('<div class="frappe-card" style="margin-bottom:15px;padding:12px;"></div>');
    const stats = [
        { label: __("Órdenes"), value: total_pos },
        { label: __("Ítems totales"), value: total_items },
        { label: __("Coinciden"), value: items_ok, color: "green" },
        { label: __("Con diferencia"), value: items_diff, color: items_diff ? "red" : "" },
    ];

    const $row = $('<div style="display:flex;gap:24px;flex-wrap:wrap;"></div>').appendTo($resumen);
    stats.forEach((s) => {
        const $cell = $('<div></div>');
        $('<div style="font-size:11px;text-transform:uppercase;color:#888;"></div>')
            .text(s.label)
            .appendTo($cell);
        $('<div style="font-size:20px;font-weight:600;"></div>')
            .text(s.value)
            .css("color", s.color || "")
            .appendTo($cell);
        $row.append($cell);
    });

    $container.append($resumen);
}

function render_buscador($container) {
    const $input = $(`<input type="text" class="form-control" placeholder="${__("Buscar por PO, código o EAN…")}" style="margin-bottom:12px;max-width:400px;">`);
    $input.on("input", function () {
        const q = $(this).val().toLowerCase();
        $container.find("[data-search]").each(function () {
            const haystack = $(this).attr("data-search").toLowerCase();
            $(this).toggle(!q || haystack.includes(q));
        });
    });
    $container.append($input);
}

function render_tablas($container, validacion) {
    validacion.forEach((order) => {
        if (!order.items || !order.items.length) return;

        const po_search = order.po || "";
        const $block = $('<div></div>').attr("data-search", po_search);

        $('<h5 style="margin-top:15px;"></h5>')
            .text(`PO: ${order.po}`)
            .appendTo($block);

        const $table = $(`
            <table class="table table-bordered table-sm">
                <thead>
                    <tr>
                        <th>${__("Código")}</th>
                        <th>${__("EAN")}</th>
                        <th>${__("Esperado")}</th>
                        <th>${__("Distribuido")}</th>
                        <th>${__("Diferencia")}</th>
                        <th>${__("Estado")}</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        `);
        const $tbody = $table.find("tbody");

        order.items.forEach((item) => {
            const search = `${order.po} ${item.intCode || ""} ${item.ean || ""}`;
            const $tr = $("<tr></tr>").attr("data-search", search);
            $("<td></td>").text(item.intCode ?? "").appendTo($tr);
            $("<td></td>").text(item.ean ?? "").appendTo($tr);
            $('<td style="text-align:right"></td>').text(item.expectedQuantity ?? "").appendTo($tr);
            $('<td style="text-align:right"></td>').text(item.distributedQuantity ?? "").appendTo($tr);
            $('<td style="text-align:right"></td>').text(item.difference ?? "").appendTo($tr);
            $('<td style="text-align:center;font-weight:bold"></td>')
                .text(item.match ? "✓" : "✗")
                .css("color", item.match ? "green" : "red")
                .appendTo($tr);
            $tbody.append($tr);
        });

        $block.append($table);
        $container.append($block);
    });
}

function mostrar_payload(frm) {
    let pretty = frm.doc.payload_original;
    try {
        pretty = JSON.stringify(JSON.parse(frm.doc.payload_original), null, 2);
    } catch (e) {
        // dejamos el texto crudo si no parsea
    }

    const d = new frappe.ui.Dialog({
        title: __("Payload original (API Walmart)"),
        size: "extra-large",
        fields: [
            {
                fieldname: "payload",
                fieldtype: "Code",
                options: "JSON",
                read_only: 1,
                default: pretty,
            },
        ],
        primary_action_label: __("Copiar al portapapeles"),
        primary_action: () => {
            navigator.clipboard.writeText(pretty).then(
                () => frappe.show_alert({ message: __("Copiado"), indicator: "green" }),
                () => frappe.show_alert({ message: __("No se pudo copiar"), indicator: "red" })
            );
        },
    });
    d.show();
}

function exportar_csv(frm) {
    const data = parse_detalle(frm);
    if (!data) return;

    const validacion = data.validacion || [];
    const headers = ["PO", "Código", "EAN", "Esperado", "Distribuido", "Diferencia", "Coincide"];
    const rows = [headers];

    validacion.forEach((order) => {
        (order.items || []).forEach((item) => {
            rows.push([
                order.po,
                item.intCode,
                item.ean,
                item.expectedQuantity,
                item.distributedQuantity,
                item.difference,
                item.match ? "Sí" : "No",
            ]);
        });
    });

    const csv = rows
        .map((r) =>
            r
                .map((cell) => {
                    const v = cell == null ? "" : String(cell);
                    return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
                })
                .join(",")
        )
        .join("\n");

    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `edinova_validacion_${frm.doc.name}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
