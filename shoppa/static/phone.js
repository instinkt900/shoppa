// Phone UI glue. Adding, ticking off and re-adding are all plain HTMX; what's
// left to wire by hand is the long press (a gesture HTMX has no trigger for), the
// two dialogs it opens, and the home-screen icon badge.
(function () {
  "use strict";

  // --- long press -------------------------------------------------------------

  const LONG_PRESS_MS = 500;
  const MOVE_TOLERANCE = 12;   // px of finger drift still counted as a press, not a scroll

  let pressTimer = null;
  let pressOrigin = null;
  let suppressClick = false;
  let target = null;           // {id, name, note} of the tile being acted on

  function cancelPress() {
    if (pressTimer) clearTimeout(pressTimer);
    pressTimer = null;
    pressOrigin = null;
  }

  const byId = (id) => document.getElementById(id);
  const menu = () => byId("menu-modal");
  const editor = () => byId("edit-modal");
  const editForm = () => byId("edit-form");

  document.addEventListener("pointerdown", (e) => {
    // A fresh press: whatever the last one armed is spent, even if its click
    // never arrived (a cancelled pointer sequence produces none).
    suppressClick = false;
    const card = e.target.closest && e.target.closest(".card[data-id]");
    if (!card) return;
    pressOrigin = { x: e.clientX, y: e.clientY };
    pressTimer = setTimeout(() => {
      cancelPress();
      // The release still fires a click; swallow it, or a long press would also
      // tick the item off / put it back.
      suppressClick = true;
      if (navigator.vibrate) navigator.vibrate(15);
      openMenu(card);
    }, LONG_PRESS_MS);
  });

  document.addEventListener("pointermove", (e) => {
    if (!pressOrigin) return;
    if (Math.abs(e.clientX - pressOrigin.x) > MOVE_TOLERANCE ||
        Math.abs(e.clientY - pressOrigin.y) > MOVE_TOLERANCE) {
      cancelPress();   // they're scrolling the list, not holding a tile
    }
  });
  ["pointerup", "pointercancel", "scroll"].forEach((type) =>
    document.addEventListener(type, cancelPress, true));

  // Capture phase, so HTMX's own handler on the tile button never sees the click.
  document.addEventListener("click", (e) => {
    if (!suppressClick) return;
    suppressClick = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);

  // Android pops a text-selection callout on a long press otherwise.
  document.addEventListener("contextmenu", (e) => {
    if (e.target.closest && e.target.closest(".card[data-id]")) e.preventDefault();
  });

  // --- edit / delete menu -----------------------------------------------------

  function openMenu(card) {
    const dlg = menu();
    if (!dlg) return;
    // Read the tile's own data-* — the board already knows everything the editor
    // needs, so neither dialog costs a round trip to open.
    target = { id: card.dataset.id, name: card.dataset.name || "", note: card.dataset.note || "" };
    const title = byId("menu-title");
    if (title) title.textContent = target.name;
    dlg.showModal();
  }

  function openEditor() {
    const dlg = editor();
    const form = editForm();
    if (!dlg || !form || !target) return;
    form.dataset.id = target.id;
    form.elements.name.value = target.name;
    form.elements.note.value = target.note;
    setEditError("");
    dlg.showModal();
    // Deferred: focusing while the dialog is still animating open gets ignored on
    // Android, and with it the keyboard that should come up with it.
    setTimeout(() => form.elements.name.focus(), 50);
  }

  function deleteTarget() {
    if (!target) return;
    // The one destructive action in the app, and the only one that asks — a
    // delete is the only thing here that can't be undone with another tap.
    if (!window.confirm("Delete “" + target.name + "” permanently?")) return;
    htmx.ajax("DELETE", "/api/items/" + target.id, { target: "#board", swap: "outerHTML" });
  }

  function setEditError(message) {
    const p = byId("edit-error");
    if (p) p.textContent = message || "";
  }

  function initDialogs() {
    const menuDlg = menu();
    const editDlg = editor();
    const form = editForm();
    if (!menuDlg || !editDlg || !form) return;

    menuDlg.addEventListener("click", (e) => {
      const action = e.target.dataset && e.target.dataset.action;
      // Clicking the backdrop targets the dialog itself.
      if (e.target === menuDlg || e.target.hasAttribute("data-close-modal")) {
        menuDlg.close();
        return;
      }
      if (!action) return;
      // Close first: two open <dialog>s would stack their backdrops, and the
      // menu has served its purpose either way.
      menuDlg.close();
      if (action === "edit") openEditor();
      else if (action === "delete") deleteTarget();
    });

    form.addEventListener("submit", (e) => {
      // Native validation has already passed by the time submit fires. The
      // request goes through htmx.ajax rather than hx-patch because htmx reads
      // the URL when it processes the element, and the item id isn't known until
      // a tile is pressed — so the response still swaps #board like every other
      // mutation.
      e.preventDefault();
      setEditError("");
      htmx.ajax("PATCH", "/api/items/" + form.dataset.id, {
        source: form,
        target: "#board",
        swap: "outerHTML",
      });
    });

    form.addEventListener("htmx:afterRequest", (e) => {
      if (e.detail.successful) {
        editDlg.close();
      } else if (e.detail.xhr && e.detail.xhr.status === 409) {
        // The dedupe key is (name, note), so this means the edit would have
        // collided with an entry that already exists. Say which case it is.
        setEditError("You already have an entry with that name and note.");
      } else {
        setEditError("Couldn't save that — check the fields and try again.");
      }
    });

    editDlg.addEventListener("click", (e) => {
      if (e.target === editDlg || e.target.hasAttribute("data-close-modal")) editDlg.close();
    });
  }

  // --- home-screen icon badge -------------------------------------------------

  // Kept in step with the board on every swap, so clearing the last item clears
  // the badge immediately. Only an installed PWA shows it; elsewhere it no-ops.
  function syncBadge() {
    const board = byId("board");
    if (!board || !navigator.setAppBadge) return;
    const n = parseInt(board.dataset.pending || "0", 10) || 0;
    const p = n > 0 ? navigator.setAppBadge(n) : navigator.clearAppBadge();
    if (p && p.catch) p.catch(() => {});
  }

  window.addEventListener("load", initDialogs);
  window.addEventListener("load", syncBadge);
  document.body.addEventListener("htmx:afterSwap", syncBadge);
})();
