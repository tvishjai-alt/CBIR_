/**
 * case_view.js
 * Radiologist diagnostic reading interface with CBIR tracking and report submission.
 */

const viewedSimilarCases = new Set();
let reviewTimerInterval = null;
let reviewSeconds = 0;

document.addEventListener("DOMContentLoaded", () => {
  initImageControls();
  initSimilarCasesTracking();
  initReportForm();
});

// Image Windowing & Contrast Controls
function initImageControls() {
  const img = document.getElementById("main-scan-img");
  if (!img) return;

  window.adjustContrast = (contrast, brightness, invert = false) => {
    let filterStr = `contrast(${contrast}%) brightness(${brightness}%)`;
    if (invert) filterStr += " invert(100%)";
    img.style.filter = filterStr;
  };

  window.resetImage = () => {
    img.style.filter = "none";
  };
}

// Track Clicks and Inspections on Similar Cases
function initSimilarCasesTracking() {
  const cards = document.querySelectorAll(".similar-card");
  const counterEl = document.getElementById("viewed-counter");

  cards.forEach(card => {
    card.addEventListener("click", () => {
      const caseId = card.dataset.caseId;
      card.classList.add("viewed");
      viewedSimilarCases.add(caseId);

      if (counterEl) {
        counterEl.textContent = `${viewedSimilarCases.size} of ${cards.length} examined`;
      }

      // Preview similar image in a small floating inspect or toggle highlight
      cards.forEach(c => c.style.outline = "none");
      card.style.outline = "2px solid var(--accent-cyan)";
    });
  });
}

// Review Lifecycle and Report Submission
function initReportForm() {
  const status = document.getElementById("case-status-badge")?.dataset.status;
  const startBtn = document.getElementById("btn-start-review");
  const submitBtn = document.getElementById("btn-submit-report");
  const reportText = document.getElementById("report-text");
  const timerEl = document.getElementById("review-timer");

  // If already In_Review on page load, start timer
  if (status === "In_Review") {
    startTimer();
  }

  // Quick phrase templates
  window.insertPhrase = (text) => {
    if (reportText && !reportText.disabled) {
      if (reportText.value.trim().length > 0) {
        reportText.value += "\n" + text;
      } else {
        reportText.value = text;
      }
      reportText.focus();
    }
  };

  // Start Review action
  if (startBtn) {
    startBtn.addEventListener("click", async () => {
      const caseId = startBtn.dataset.caseId;
      const radId = startBtn.dataset.radId;

      startBtn.disabled = true;
      startBtn.textContent = "Starting...";

      try {
        const res = await fetch(`/api/case/${caseId}/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rad_id: radId }),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.message || "Failed to start review");

        // Update UI to In_Review
        startBtn.style.display = "none";
        if (submitBtn) submitBtn.disabled = false;
        if (reportText) {
          reportText.disabled = false;
          reportText.placeholder = "Enter your radiological observations and impressions...";
          reportText.focus();
        }

        const badge = document.getElementById("case-status-badge");
        if (badge) {
          badge.textContent = "In_Review";
          badge.className = "badge badge-status";
        }

        startTimer();
      } catch (err) {
        alert("Error starting review: " + err.message);
        startBtn.disabled = false;
        startBtn.textContent = "Start Review";
      }
    });
  }

  // Submit Report action
  if (submitBtn) {
    submitBtn.addEventListener("click", async () => {
      const caseId = submitBtn.dataset.caseId;
      const radId = submitBtn.dataset.radId;
      const text = reportText.value.trim();

      if (!text) {
        alert("Please enter diagnostic report findings before submitting.");
        reportText.focus();
        return;
      }

      submitBtn.disabled = true;
      submitBtn.textContent = "Submitting Report...";

      const viewedCount = viewedSimilarCases.size;
      const usedSimilar = viewedCount > 0 ? 1 : 0;

      try {
        const res = await fetch(`/api/case/${caseId}/report`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            rad_id: radId,
            report_text: text,
            similar_cases_viewed: viewedCount,
            used_similar_cases: usedSimilar,
          }),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.message || "Failed to submit report");

        // Stop timer
        if (reviewTimerInterval) clearInterval(reviewTimerInterval);

        // Update UI
        const badge = document.getElementById("case-status-badge");
        if (badge) {
          badge.textContent = "Reported";
          badge.className = "badge badge-routine";
        }

        submitBtn.style.display = "none";
        reportText.disabled = true;

        const returnBtn = document.getElementById("btn-return-clinic");
        if (returnBtn) returnBtn.style.display = "inline-flex";

        alert("Report submitted successfully! Turnaround recorded.");
      } catch (err) {
        alert("Error submitting report: " + err.message);
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit Diagnostic Report";
      }
    });
  }

  // Return to Clinic action
  const returnBtn = document.getElementById("btn-return-clinic");
  if (returnBtn) {
    returnBtn.addEventListener("click", async () => {
      const caseId = returnBtn.dataset.caseId;
      returnBtn.disabled = true;
      try {
        const res = await fetch(`/api/case/${caseId}/return`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.message || "Failed to return to clinic");

        const badge = document.getElementById("case-status-badge");
        if (badge) {
          badge.textContent = "Returned_To_Clinic";
          badge.className = "badge badge-routine";
        }
        returnBtn.style.display = "none";
        alert("Case successfully returned to referring clinic.");
      } catch (err) {
        alert("Error returning case: " + err.message);
        returnBtn.disabled = false;
      }
    });
  }
}

function startTimer() {
  const timerEl = document.getElementById("review-timer");
  if (!timerEl) return;

  if (reviewTimerInterval) clearInterval(reviewTimerInterval);
  reviewSeconds = 0;

  reviewTimerInterval = setInterval(() => {
    reviewSeconds++;
    const mins = Math.floor(reviewSeconds / 60);
    const secs = reviewSeconds % 60;
    timerEl.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }, 1000);
}
