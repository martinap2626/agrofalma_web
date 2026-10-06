document.addEventListener("DOMContentLoaded", () => {
    const paleta = ["#1f5c3b", "#2b5fa8", "#e0a531", "#6a3fa0", "#d64545", "#6a6f68"];

    // con el modo privacidad activo, los gráficos no muestran cifras de dinero (ejes ni globos de información)
    const privado = () => document.documentElement.classList.contains("privado");
    const graficos = [];
    const sinMontos = {
        plugins: { tooltip: { callbacks: { label: (ctx) => (privado() ? ctx.label || "" : ctx.formattedValue) } } },
    };

    const graficoCategoria = document.getElementById("grafico-categoria");
    if (graficoCategoria) {
        graficos.push(new Chart(graficoCategoria, {
            type: "doughnut",
            data: {
                labels: JSON.parse(graficoCategoria.dataset.labels || "[]"),
                datasets: [{
                    data: JSON.parse(graficoCategoria.dataset.valores || "[]"),
                    backgroundColor: paleta,
                }],
            },
            options: { plugins: { legend: { position: "right" }, tooltip: sinMontos.plugins.tooltip } },
        }));
    }

    const graficoTrabajador = document.getElementById("grafico-trabajador");
    if (graficoTrabajador) {
        graficos.push(new Chart(graficoTrabajador, {
            type: "bar",
            data: {
                labels: JSON.parse(graficoTrabajador.dataset.labels || "[]"),
                datasets: [{
                    data: JSON.parse(graficoTrabajador.dataset.valores || "[]"),
                    backgroundColor: paleta[0],
                }],
            },
            options: {
                indexAxis: "y",
                plugins: { legend: { display: false }, tooltip: sinMontos.plugins.tooltip },
                scales: { x: { beginAtZero: true, ticks: { callback: (valor) => (privado() ? "" : valor) } } },
            },
        }));
    }

    // al activar o desactivar el modo privacidad se redibujan los gráficos
    document.addEventListener("privacidad-cambio", () => graficos.forEach((g) => g.update()));
});
