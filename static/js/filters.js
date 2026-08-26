/* RFI 列表的多選篩選器：開合下拉、選單內搜尋、勾選即自動套用。
   關閉 JS 時仍可用（noscript 會顯示「套用篩選」按鈕），因此所有狀態
   都放在原生 form 的 checkbox / input 上，這裡只負責互動與自動送出。 */
(function () {
    "use strict";

    var form = document.getElementById("filterForm");
    if (!form) return;

    function closeAll(except) {
        form.querySelectorAll(".filter-menu").forEach(function (menu) {
            if (menu !== except) menu.hidden = true;
        });
    }

    // 下拉開合
    form.querySelectorAll("[data-toggle]").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
            e.stopPropagation();
            var menu = document.getElementById("menu_" + btn.dataset.toggle);
            if (!menu) return;
            var willOpen = menu.hidden;
            closeAll(menu);
            menu.hidden = !willOpen;
            if (willOpen) {
                var search = menu.querySelector("[data-search]");
                if (search) search.focus();
            }
        });
    });

    // 點選單以外的地方就收合
    document.addEventListener("click", function (e) {
        if (!e.target.closest(".filter-item")) closeAll(null);
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closeAll(null);
    });

    // 選單內搜尋：即時過濾選項
    form.querySelectorAll("[data-search]").forEach(function (input) {
        input.addEventListener("input", function () {
            var term = input.value.trim().toLowerCase();
            var menu = input.closest(".filter-menu");
            var shown = 0;
            menu.querySelectorAll(".filter-option").forEach(function (opt) {
                var hit = !term || (opt.dataset.value || "").indexOf(term) >= 0;
                opt.style.display = hit ? "" : "none";
                if (hit) shown++;
            });
            var empty = menu.querySelector(".menu-empty");
            if (empty) empty.style.display = shown ? "none" : "";
        });
        // 選單內的搜尋框不應觸發表單送出
        input.addEventListener("keydown", function (e) {
            if (e.key === "Enter") e.preventDefault();
        });
    });

    // 勾選即套用
    form.querySelectorAll('.filter-options input[type="checkbox"]').forEach(function (cb) {
        cb.addEventListener("change", function () { form.submit(); });
    });

    // 清除單一欄位的勾選（若原本沒勾選就不必重新整理）
    form.querySelectorAll("[data-clear]").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
            e.stopPropagation();
            var menu = document.getElementById("menu_" + btn.dataset.clear);
            var changed = false;
            menu.querySelectorAll('input[type="checkbox"]:checked').forEach(function (cb) {
                cb.checked = false;
                changed = true;
            });
            if (changed) form.submit();
        });
    });

    // 關鍵字：停止輸入 400ms 後自動搜尋
    var q = document.getElementById("q");
    if (q) {
        var timer = null;
        q.addEventListener("input", function () {
            clearTimeout(timer);
            timer = setTimeout(function () { form.submit(); }, 400);
        });
    }
})();
