import { list } from "./dom.js";
import { rise } from "./rise.js";
import { REVEAL_INSET, STAGGER } from "./tokens.js";
const style = (el) => el.style;
/** Hide the targets now and rise each one the first time it scrolls into view. Returns disconnect. */
export function reveal(targets, { targets: scope = "self", stagger = STAGGER.reveal, root = null } = {}) {
    const pending = new Map(list(targets, scope).map((el) => [el, {
            opacity: style(el).opacity,
            priority: style(el).getPropertyPriority("opacity"),
        }]));
    const running = new Set();
    const restore = (el) => {
        const original = pending.get(el);
        if (original.opacity)
            style(el).setProperty("opacity", original.opacity, original.priority);
        else
            style(el).removeProperty("opacity");
        pending.delete(el);
    };
    const io = new IntersectionObserver((entries) => {
        let i = 0;
        for (const entry of entries) {
            if (!entry.isIntersecting || !pending.has(entry.target))
                continue;
            restore(entry.target);
            for (const animation of rise(entry.target, { delay: i++ * stagger })) {
                running.add(animation);
                animation.finished.then(() => running.delete(animation), () => running.delete(animation));
            }
            io.unobserve(entry.target);
        }
    }, { root, rootMargin: `0px 0px -${(root ? root.clientHeight : window.innerHeight) * REVEAL_INSET}px 0px` });
    for (const el of pending.keys()) {
        style(el).opacity = "0";
        io.observe(el);
    }
    return () => {
        io.disconnect();
        for (const el of pending.keys())
            restore(el);
        for (const animation of running)
            animation.cancel();
        running.clear();
    };
}
