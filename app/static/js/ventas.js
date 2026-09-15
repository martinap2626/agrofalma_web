/*
 * Formulario de nueva venta: permite agregar/quitar líneas de producto y
 * recalcula el total en vivo (solo para que el usuario vea el total
 * mientras completa el formulario — el servidor siempre vuelve a calcular
 * subtotales y total antes de guardar, nunca confía en estos valores).
 */
document.addEventListener("DOMContentLoaded", function () {
    const tabla = document.getElementById("tabla-lineas-venta");
    const cuerpo = tabla ? tabla.querySelector("tbody") : null;
    const plantilla = document.getElementById("plantilla-linea-venta");
    const botonAgregar = document.getElementById("boton-agregar-linea");
    const totalVenta = document.getElementById("total-venta");
    if (!cuerpo || !plantilla || !botonAgregar) return;

    function recalcularTotal() {
        let total = 0;
        cuerpo.querySelectorAll("tr").forEach(function (fila) {
            const cantidad = parseFloat(fila.querySelector('[name="cantidad[]"]').value) || 0;
            const precio = parseFloat(fila.querySelector('[name="precio_unitario[]"]').value) || 0;
            const subtotal = cantidad * precio;
            fila.querySelector(".subtotal-linea").textContent = "S/ " + subtotal.toFixed(2);
            total += subtotal;
        });
        totalVenta.textContent = "Total: S/ " + total.toFixed(2);
    }

    function activarFila(fila) {
        const selectProducto = fila.querySelector('[name="id_producto[]"]');
        const inputPrecio = fila.querySelector('[name="precio_unitario[]"]');
        const inputCantidad = fila.querySelector('[name="cantidad[]"]');
        const botonQuitar = fila.querySelector(".boton-quitar-linea");

        selectProducto.addEventListener("change", function () {
            const opcion = selectProducto.selectedOptions[0];
            inputPrecio.value = opcion ? (opcion.getAttribute("data-precio") || "") : "";
            recalcularTotal();
        });
        inputPrecio.addEventListener("input", recalcularTotal);
        inputCantidad.addEventListener("input", recalcularTotal);
        botonQuitar.addEventListener("click", function () {
            if (cuerpo.querySelectorAll("tr").length > 1) {
                fila.remove();
                recalcularTotal();
            }
        });
    }

    cuerpo.querySelectorAll("tr").forEach(activarFila);

    botonAgregar.addEventListener("click", function () {
        const nuevaFila = plantilla.content.firstElementChild.cloneNode(true);
        cuerpo.appendChild(nuevaFila);
        activarFila(nuevaFila);
    });
});
