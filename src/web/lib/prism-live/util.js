// Local dependency adapter for Prism Live.
//
// Prism Live's upstream utility imports a handful of helpers from
// https://v2.blissfuljs.com. Runtime third-party scripts are not allowed in
// this application, so the small subset Prism Live uses is implemented here
// with standard DOM APIs. Modified for 3DMigoto Mod Viewer, 2026-08-14.

export function $(selector, context = document) {
	return context.querySelector(selector);
}

export function $$(selector, context = document) {
	return Array.from(context.querySelectorAll(selector));
}

$.create = function create(tag = "div", properties = {}) {
	if (typeof tag !== "string") {
		properties = tag;
		tag = "div";
	}

	const element = document.createElement(tag);
	const { around, before, after, contents, ...rest } = properties;
	Object.assign(element, rest);

	if (contents instanceof Node) {
		element.append(contents);
	}
	else if (contents !== undefined && contents !== null) {
		element.textContent = String(contents);
	}

	if (around) {
		around.before(element);
		element.append(around);
	}
	else if (before) {
		before.before(element);
	}
	else if (after) {
		after.after(element);
	}

	return element;
};

$.bind = function bind(target, handlers) {
	for (const [events, handler] of Object.entries(handlers)) {
		for (const event of events.trim().split(/\s+/)) {
			target.addEventListener(event, handler);
		}
	}
	return target;
};

// Prism Live exports this helper even when no dynamic resources are requested.
// Keep it local and CSP-safe for future use.
$.load = function load(url) {
	return new Promise((resolve, reject) => {
		const link = document.createElement("link");
		link.rel = "stylesheet";
		link.href = String(url);
		link.addEventListener("load", () => resolve(link), { once: true });
		link.addEventListener("error", reject, { once: true });
		document.head.append(link);
	});
};

/** Escape a value before inserting it into a regular expression. */
const escape = value => value.replace(/[-\/\\^$*+?.()|[\]{}]/g, "\\$&");
const makeRegexp = (flags, strings, ...values) => {
	const pattern = strings[0] + values.map((value, index) =>
		escape(value) + strings[index + 1]).join("");
	return RegExp(pattern, flags);
};
const cache = {};

export const regexp = new Proxy(makeRegexp.bind(null, ""), {
	get: (target, property) => target[property]
		|| cache[property]
		|| (cache[property] = makeRegexp.bind(null, property)),
});

export function loadLanguages(ids, PrismLive) {
	ids = Array.isArray(ids) ? ids : ids.split(/,/);
	return ids.map(id => import(`./languages/${id}.mjs`).then(module => {
		if (module.default) {
			PrismLive.registerLanguage(module.default.id, module.default);
		}
		else {
			for (const name in module) {
				if (PrismLive.languages[name]) {
					Object.assign(PrismLive.languages[name], module[name]);
				}
				else {
					PrismLive.registerLanguage(name, module[name]);
				}
			}
		}
	}));
}
