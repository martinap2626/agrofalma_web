/*
 * Filtro dinámico reutilizable para las tablas de listado (productos,
 * ventas, clientes, usuarios): filtra las filas ya cargadas mientras el
 * usuario escribe, sin recargar la página.
 *
 * Uso: <input data-buscador="id-de-la-tabla"> y en cada <tr data-filtro="texto a comparar">.
 */
document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-buscador]").forEach(function (buscador) {
        const tabla = document.getElementById(buscador.getAttribute("data-buscador"));
        if (!tabla) return;

        const filas = tabla.querySelectorAll("tbody tr[data-filtro]");

        buscador.addEventListener("input", function () {
            const texto = buscador.value.trim().toLowerCase();
            filas.forEach(function (fila) {
                const filtro = fila.getAttribute("data-filtro");
                fila.style.display = filtro.includes(texto) ? "" : "none";
            });
        });
    });
});
