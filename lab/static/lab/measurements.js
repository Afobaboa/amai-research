"use strict";
{
    function measurementColumn(input) {
        return input.hasAttribute('data-measurement-value') ? 'value' : 'reason';
    }
    function measurementInputs(table, column) {
        const selector = column === 'value'
            ? '[data-measurement-value]' : '.measurement-reason-cell input';
        return [...table.querySelectorAll(selector)].filter(input => (!input.disabled && !input.readOnly && !input.closest('[hidden]')));
    }
    function moveMeasurementFocus(event, table) {
        if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing)
            return;
        const input = event.target;
        if (!(input instanceof HTMLInputElement))
            return;
        const enter = event.key === 'Enter';
        const arrow = input.type === 'number'
            && ['ArrowUp', 'ArrowDown'].includes(event.key);
        if (!enter && !arrow)
            return;
        const inputs = measurementInputs(table, measurementColumn(input));
        const index = inputs.indexOf(input);
        if (index < 0)
            return;
        event.preventDefault();
        const backwards = event.key === 'ArrowUp' || (enter && event.shiftKey);
        const next = inputs[index + (backwards ? -1 : 1)];
        next?.focus();
        next?.select();
    }
    document.querySelectorAll('.measurement-table').forEach(table => {
        table.addEventListener('keydown', event => moveMeasurementFocus(event, table));
    });
}
