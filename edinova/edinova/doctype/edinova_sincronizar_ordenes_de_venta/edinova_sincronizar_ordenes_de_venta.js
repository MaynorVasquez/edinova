frappe.ui.form.on("Edinova Sincronizar Ordenes de Venta", {
    refresh(frm) {
        let $wrapper = frm.fields_dict.detalle.$wrapper;
        $wrapper.find('.detalle-table').remove();

        if (!frm.doc.detalle) return;

        try {
            let data = JSON.parse(frm.doc.detalle);
            let validacion = data.validacion || [];
            let html = '';

            validacion.forEach(order => {
                if (!order.items || !order.items.length) return;

                html += `<h5 style="margin-top:15px;">PO: ${order.po}</h5>`;
                html += `<table class="table table-bordered table-sm">
                    <thead>
                        <tr>
                            <th>Código</th>
                            <th>EAN</th>
                            <th>Esperado</th>
                            <th>Distribuido</th>
                            <th>Diferencia</th>
                            <th>Estado</th>
                        </tr>
                    </thead>
                    <tbody>`;

                order.items.forEach(item => {
                    let color = item.match ? 'green' : 'red';
                    let icon = item.match ? '✓' : '✗';
                    html += `<tr>
                        <td>${item.intCode}</td>
                        <td>${item.ean}</td>
                        <td style="text-align:right">${item.expectedQuantity}</td>
                        <td style="text-align:right">${item.distributedQuantity}</td>
                        <td style="text-align:right">${item.difference}</td>
                        <td style="text-align:center;color:${color};font-weight:bold">${icon}</td>
                    </tr>`;
                });

                html += `</tbody></table>`;
            });

            $wrapper.append(`<div class="detalle-table">${html}</div>`);

        } catch (e) {
            console.error('Error parseando detalle:', e);
        }
    },
});