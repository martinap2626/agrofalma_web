document.addEventListener("DOMContentLoaded", () => {
    const paleta = ["#1f5c3b", "#2b5fa8", "#e0a531", "#6a3fa0", "#d64545", "#6a6f68"];

    const graficoCategoria = document.getElementById("grafico-categoria");
    if (graficoCategoria) {
        new Chart(graficoCategoria, {
            type: "doughnut",
            data: {
                labels: JSON.parse(graficoCategoria.dataset.labels || "[]"),
                datasets: [{
                    data: JSON.parse(graficoCategoria.dataset.valores || "[]"),
                    backgroundColor: paleta,
                }],
            },
            options: { plugins: { legend: { position: "right" } } },
        });
    }

    const graficoTrabajador = document.getElementById("grafico-trabajador");
    if (graficoTrabajador) {
        new Chart(graficoTrabajador, {
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
                plugins: { legend: { display: false } },
                scales: { x: { beginAtZero: true } },
            },
        });
    }
});
