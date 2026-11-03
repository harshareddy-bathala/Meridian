export const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
export const list = (targets, scope = "self") => {
    const elements = typeof targets === "string"
        ? [...document.querySelectorAll(targets)]
        : targets instanceof Element
            ? [targets]
            : [...targets];
    return [...new Set(scope === "children" ? elements.flatMap((el) => [...el.children]) : elements)];
};
/** Cancel everything running on the element so a new motion never stacks on an old fill. */
export const clear = (el) => el.getAnimations().forEach((a) => a.cancel());
/** True while any animation is running or filling on the element. */
export const moving = (el) => el.getAnimations().length > 0;
/**
 * The element's current opacity and transform-related values, read before `clear` so a
 * motion that interrupts another continues from where it is instead of snapping to a
 * fixed first keyframe. Only the keys asked for are returned.
 */
export const current = (el, keys) => {
    const cs = getComputedStyle(el);
    const fallback = { opacity: "1", translate: "none", scale: "none", filter: "none" };
    return Object.fromEntries(keys.map((k) => [k, cs[k] || fallback[k]]));
};
