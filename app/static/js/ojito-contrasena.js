/*
 * Botón de "ojito" para mostrar/ocultar contraseña. Funciona con
 * cualquier cantidad de campos en la misma página (login: 1 campo;
 * usuarios: contraseña + confirmar). Requiere el partial
 * templates/_campo_contrasena.html (input + .boton-ojo con sus dos íconos).
 */
document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".campo-contrasena").forEach(function (campo) {
        const input = campo.querySelector("input");
        const boton = campo.querySelector(".boton-ojo");
        const iconoAbierto = campo.querySelector(".icono-ojo-abierto");
        const iconoCerrado = campo.querySelector(".icono-ojo-cerrado");
        if (!input || !boton) return;

        boton.addEventListener("click", function () {
            const ahoraVisible = input.type === "password";
            input.type = ahoraVisible ? "text" : "password";
            iconoAbierto.classList.toggle("icono-ojo-oculto", ahoraVisible);
            iconoCerrado.classList.toggle("icono-ojo-oculto", !ahoraVisible);
            boton.setAttribute("aria-pressed", String(ahoraVisible));
            boton.setAttribute("aria-label", ahoraVisible ? "Ocultar contraseña" : "Mostrar contraseña");
        });
    });
});
