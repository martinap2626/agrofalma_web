/*
 * Gráfico de agotamiento del simulador de escenarios: cómo baja el stock día a día en la situación actual y en el escenario,
 * con una línea vertical roja en el día en que llegaría la reposición y una horizontal punteada en el punto de reposición.
 * Los datos los entrega el servidor en el atributo data-grafico del <canvas>; este archivo solo los dibuja.
 */
document.addEventListener("DOMContentLoaded", function () {
    const lienzo = document.getElementById("grafico-simulador");
    if (!lienzo || typeof Chart === "undefined") return;
    const datos = JSON.parse(lienzo.dataset.grafico || "null");
    if (!datos) return;

    // dibuja las dos líneas de referencia (llegada de la reposición y punto de reposición) encima del gráfico
    const referencias = {
        id: "referencias",
        afterDatasetsDraw(grafico) {
            const { ctx, chartArea, scales } = grafico;
            ctx.save();
            if (datos.dia_reposicion !== null && datos.dia_reposicion !== undefined) {
                const x = scales.x.getPixelForValue(datos.dia_reposicion);
                ctx.strokeStyle = "#b3261e";
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(x, chartArea.top);
                ctx.lineTo(x, chartArea.bottom);
                ctx.stroke();
                ctx.fillStyle = "#b3261e";
                ctx.font = "600 12px sans-serif";
                ctx.textAlign = x > chartArea.right - 130 ? "right" : "left";
                ctx.fillText("Llega la reposición", x + (ctx.textAlign === "left" ? 6 : -6), chartArea.top + 14);
            }
            if (datos.punto_reposicion > 0) {
                const y = scales.y.getPixelForValue(datos.punto_reposicion);
                ctx.setLineDash([6, 5]);
                ctx.strokeStyle = "#8a5a00";
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.moveTo(chartArea.left, y);
                ctx.lineTo(chartArea.right, y);
                ctx.stroke();
                ctx.setLineDash([]);
                ctx.fillStyle = "#8a5a00";
                ctx.font = "12px sans-serif";
                ctx.textAlign = "left";
                ctx.fillText("Punto de reposición", chartArea.left + 6, y - 6);
            }
            ctx.restore();
        },
    };

    new Chart(lienzo, {
        type: "line",
        data: {
            labels: datos.etiquetas,
            datasets: [
                { label: "Situación actual", data: datos.normal, borderColor: "#6a6f68", backgroundColor: "#6a6f68", borderDash: [5, 4], borderWidth: 2, pointRadius: 0, tension: 0 },
                { label: "Escenario", data: datos.escenario, borderColor: "#1f5c3b", backgroundColor: "#1f5c3b", borderWidth: 3, pointRadius: 0, tension: 0 },
            ],
        },
        options: {
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: { position: "bottom" },
                tooltip: { callbacks: { title: (items) => "Día " + items[0].dataIndex + " (" + items[0].label + ")", label: (ctx) => ctx.dataset.label + ": " + ctx.formattedValue + " unidades" } },
            },
            scales: {
                x: { title: { display: true, text: "Fecha (día/mes)" }, ticks: { maxTicksLimit: 12, autoSkip: true } },
                y: { beginAtZero: true, title: { display: true, text: "Unidades en almacén" } },
            },
        },
        plugins: [referencias],
    });
});
