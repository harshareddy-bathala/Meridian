import { calm, clear, current, list, moving } from "./dom.js";
import { EASE, LIFT_PX, MS, STAGGER } from "./tokens.js";
/**
 * Fade and lift each target in, one after another. Returns one Animation per element.
 * An element already in motion continues from where it is instead of restarting hidden.
 */
export function rise(targets, { targets: scope = "self", stagger = STAGGER.rise, delay = 0 } = {}) {
    const still = calm();
    const hidden = still ? { opacity: 0 } : { opacity: 0, translate: `0 ${LIFT_PX}px` };
    const shown = still ? { opacity: 1 } : { opacity: 1, translate: "0 0" };
    return list(targets, scope).map((el, i) => {
        const from = moving(el) ? current(el, still ? ["opacity"] : ["opacity", "translate"]) : hidden;
        clear(el);
        return el.animate([from, shown], {
            duration: MS.enter,
            delay: delay + i * stagger,
            easing: EASE,
            fill: "backwards",
        });
    });
}
