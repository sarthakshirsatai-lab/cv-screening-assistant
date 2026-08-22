const BAND_COLOR = {
    "Strong Fit": "green",
    "Needs Review": "amber",
    "Likely Not a Fit": "red",
};

const PAGE_SIZE = 10;
let allCandidates = [];
let currentPage = 1;

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("start-screening-btn").addEventListener("click", startScreening);
    document.getElementById("candidates-list").addEventListener("click", handleListClick);
    document.getElementById("pagination").addEventListener("click", handlePaginationClick);
});

async function startScreening() {
    const btn = document.getElementById("start-screening-btn");
    hideError();
    btn.disabled = true;
    btn.textContent = "Screening...";
    showLoadingState();

    try {
        const screenRes = await fetch("/api/screen", { method: "POST" });
        if (!screenRes.ok) throw new Error("Screening request failed");
        const runData = await screenRes.json();
        document.getElementById("last-run-subtitle").textContent = `Last run: ${runData.run_at}`;

        const resultsRes = await fetch("/api/results");
        if (!resultsRes.ok) throw new Error("Failed to load results");
        allCandidates = await resultsRes.json();
        currentPage = 1;

        renderStats(allCandidates);
        renderPage();
    } catch (err) {
        document.getElementById("candidates-list").innerHTML = "";
        document.getElementById("stats-row").innerHTML = "";
        showError("Screening failed -- please try again.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Start Screening";
    }
}

function showLoadingState() {
    document.getElementById("stats-row").innerHTML = "";
    document.getElementById("pagination").hidden = true;
    document.getElementById("pagination").innerHTML = "";
    document.getElementById("candidates-list").innerHTML = `
        <div class="loading-state">
            <div class="spinner"></div>
            <p>Screening candidates...</p>
        </div>
    `;
}

function renderStats(candidates) {
    const counts = {
        total: candidates.length,
        "Strong Fit": 0,
        "Needs Review": 0,
        "Likely Not a Fit": 0,
    };
    for (const c of candidates) {
        counts[c.fit_band] = (counts[c.fit_band] || 0) + 1;
    }

    const cards = [
        { label: "Total Candidates", value: counts.total, colorClass: "plain" },
        { label: "Strong Fit", value: counts["Strong Fit"], colorClass: "green" },
        { label: "Needs Review", value: counts["Needs Review"], colorClass: "amber" },
        { label: "Likely Not a Fit", value: counts["Likely Not a Fit"], colorClass: "red" },
    ];

    const statsRow = document.getElementById("stats-row");
    statsRow.innerHTML = cards.map(card => `
        <div class="stat-card">
            <div class="stat-label">${escapeHtml(card.label)}</div>
            <div class="stat-value stat-value--${card.colorClass}">${card.value}</div>
        </div>
    `).join("");
}

function renderPage() {
    const totalPages = Math.max(1, Math.ceil(allCandidates.length / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * PAGE_SIZE;
    const pageItems = allCandidates.slice(start, start + PAGE_SIZE);

    const list = document.getElementById("candidates-list");
    list.innerHTML = pageItems.length
        ? pageItems.map(renderCandidateRow).join("")
        : `<p class="empty-state">No candidates to show.</p>`;

    renderPagination(totalPages);
}

function renderCandidateRow(c) {
    const color = BAND_COLOR[c.fit_band] || "plain";
    const criteriaRows = c.criterion_scores.map(s => `
        <tr>
            <td>${escapeHtml(s.criterion_label)}</td>
            <td><span class="criterion-result criterion-result--${s.result.toLowerCase()}">${s.result}</span></td>
        </tr>
    `).join("");

    const shortlistSelected = c.human_decision === "Shortlist" ? "btn-decision--selected" : "";
    const passSelected = c.human_decision === "Pass" ? "btn-decision--selected" : "";

    return `
        <div class="candidate-row" data-id="${c.id}">
            <div class="candidate-row-top">
                <span class="candidate-name">${escapeHtml(c.candidate_name || "Unknown Candidate")}</span>
                <span class="badge badge--${color}">${escapeHtml(c.fit_band)}</span>
            </div>
            <p class="candidate-summary">${escapeHtml(c.llm_summary)}</p>
            <div class="candidate-row-actions">
                <button class="btn-link toggle-details">See breakdown &#9662;</button>
                <div class="decision-actions">
                    <button class="btn-decision ${shortlistSelected}" data-decision="Shortlist">Shortlist</button>
                    <button class="btn-decision ${passSelected}" data-decision="Pass">Pass</button>
                </div>
            </div>
            <div class="candidate-details" hidden>
                <table class="criteria-table">
                    <thead>
                        <tr><th>Requirement</th><th>Result</th></tr>
                    </thead>
                    <tbody>${criteriaRows}</tbody>
                </table>
            </div>
        </div>
    `;
}

function renderPagination(totalPages) {
    const pagination = document.getElementById("pagination");
    if (totalPages <= 1) {
        pagination.hidden = true;
        pagination.innerHTML = "";
        return;
    }

    let pageButtons = "";
    for (let p = 1; p <= totalPages; p++) {
        pageButtons += `<button class="btn-page-num ${p === currentPage ? "btn-page-num--active" : ""}" data-page="${p}">${p}</button>`;
    }

    pagination.hidden = false;
    pagination.innerHTML = `
        <button class="btn-page" data-page="prev" ${currentPage === 1 ? "disabled" : ""}>&laquo; Prev</button>
        ${pageButtons}
        <button class="btn-page" data-page="next" ${currentPage === totalPages ? "disabled" : ""}>Next &raquo;</button>
    `;
}

function handlePaginationClick(event) {
    const btn = event.target.closest("button[data-page]");
    if (!btn || btn.disabled) return;

    const totalPages = Math.max(1, Math.ceil(allCandidates.length / PAGE_SIZE));
    if (btn.dataset.page === "prev") {
        currentPage = Math.max(1, currentPage - 1);
    } else if (btn.dataset.page === "next") {
        currentPage = Math.min(totalPages, currentPage + 1);
    } else {
        currentPage = parseInt(btn.dataset.page, 10);
    }
    renderPage();
}

function handleListClick(event) {
    const toggleBtn = event.target.closest(".toggle-details");
    if (toggleBtn) {
        const row = toggleBtn.closest(".candidate-row");
        const details = row.querySelector(".candidate-details");
        const isHidden = details.hasAttribute("hidden");
        if (isHidden) {
            details.removeAttribute("hidden");
            toggleBtn.innerHTML = "Hide breakdown &#9652;";
        } else {
            details.setAttribute("hidden", "");
            toggleBtn.innerHTML = "See breakdown &#9662;";
        }
        return;
    }

    const decisionBtn = event.target.closest(".btn-decision");
    if (decisionBtn) {
        const row = decisionBtn.closest(".candidate-row");
        postDecision(row.dataset.id, decisionBtn.dataset.decision, decisionBtn);
    }
}

async function postDecision(candidateId, decision, btnEl) {
    try {
        const res = await fetch(`/api/candidate/${candidateId}/decision`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decision }),
        });
        if (!res.ok) throw new Error("Decision failed");

        const actions = btnEl.closest(".decision-actions");
        actions.querySelectorAll(".btn-decision").forEach(b => {
            b.classList.toggle("btn-decision--selected", b === btnEl);
        });

        const row = btnEl.closest(".candidate-row");
        const candidate = allCandidates.find(c => String(c.id) === row.dataset.id);
        if (candidate) candidate.human_decision = decision;
    } catch (err) {
        showError("Could not save decision -- please try again.");
    }
}

function showError(message) {
    const banner = document.getElementById("error-banner");
    banner.textContent = message;
    banner.hidden = false;
}

function hideError() {
    const banner = document.getElementById("error-banner");
    banner.hidden = true;
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value === null || value === undefined ? "" : String(value);
    return div.innerHTML;
}
