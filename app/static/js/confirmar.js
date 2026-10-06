/*
 * Confirmación de acciones destructivas o delicadas.
 *
 * Reemplaza el confirm() nativo del navegador por un cuadro de diálogo propio
 * (accesible: usa el elemento <dialog>, que atrapa el foco y se cierra con
 * Esc). Se activa con un atributo en el formulario:
 *
 *     <form method="POST" data-confirmar="¿Desactivar a Juan Pérez?"> ... </form>
 *
 * Opcional: data-confirmar-boton="Desactivar" cambia el texto del botón de
 * confirmación.
 *
 * Se hace en un archivo aparte (y no con onsubmit="..." en la plantilla)
 * para que la política de seguridad de contenido (CSP) pueda prohibir
 * scripts en línea.
 */
document.addEventListener("DOMContentLoaded", function () {
    var formularios = document.querySelectorAll("form[data-confirmar]");
    if (!formularios.length) return;

    var dialogo = document.createElement("dialog");
    dialogo.className = "dialogo-confirmar";
    dialogo.setAttribute("aria-labelledby", "dialogo-confirmar-mensaje");
    dialogo.innerHTML =
        '<p id="dialogo-confirmar-mensaje" class="dialogo-mensaje"></p>' +
        '<div class="dialogo-acciones">' +
        '<button type="button" class="boton-secundario" data-accion="cancelar">Cancelar</button>' +
        '<button type="button" class="boton-primario" data-accion="confirmar">Confirmar</button>' +
        "</div>";
    document.body.appendChild(dialogo);

    var mensaje = dialogo.querySelector(".dialogo-mensaje");
    var botonConfirmar = dialogo.querySelector('[data-accion="confirmar"]');
    var botonCancelar = dialogo.querySelector('[data-accion="cancelar"]');
    var formularioPendiente = null;

    botonCancelar.addEventListener("click", function () {
        formularioPendiente = null;
        dialogo.close();
    });
    botonConfirmar.addEventListener("click", function () {
        var formulario = formularioPendiente;
        formularioPendiente = null;
        dialogo.close();
        if (formulario) formulario.submit(); // submit() no dispara de nuevo el evento "submit"
    });

    formularios.forEach(function (formulario) {
        formulario.addEventListener("submit", function (evento) {
            var texto = formulario.getAttribute("data-confirmar");
            evento.preventDefault();

            // navegadores muy antiguos sin <dialog>: se cae al confirm() nativo
            if (typeof dialogo.showModal !== "function") {
                if (window.confirm(texto)) formulario.submit();
                return;
            }

            formularioPendiente = formulario;
            mensaje.textContent = texto;
            botonConfirmar.textContent = formulario.getAttribute("data-confirmar-boton") || "Confirmar";
            dialogo.showModal();
            botonCancelar.focus();
        });
    });
});
