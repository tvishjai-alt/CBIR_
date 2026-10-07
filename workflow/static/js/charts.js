/**
 * charts.js
 * Chart.js Visualizations for Hospital Operational Analytics.
 */

let workloadChartInstance = null;
let pendingPriorityChartInstance = null;
let throughputChartInstance = null;
let slaBreachChartInstance = null;

// Chart.js Global Default Theme Settings (white-and-pink theme)
Chart.defaults.color = "#7d5a6c";
Chart.defaults.font.family = "'Plus Jakarta Sans', 'Segoe UI', Roboto, sans-serif";
Chart.defaults.plugins.tooltip.backgroundColor = "#fff0f6";
Chart.defaults.plugins.tooltip.titleColor = "#3a1f2e";
Chart.defaults.plugins.tooltip.bodyColor = "#7d5a6c";
Chart.defaults.plugins.tooltip.borderColor = "#f7d3e2";
Chart.defaults.plugins.tooltip.borderWidth = 1;

async function loadHospitalAnalytics() {
  try {
    const res = await fetch("/api/hospital/analytics");
    if (!res.ok) throw new Error("Failed to fetch analytics");
    const json = await res.json();

    updateKPICards(json);
    renderWorkloadChart(json.workloads);
    renderPendingPriorityChart(json.pending_priority);
    renderThroughputChart(json.throughput);
    renderSLABreachChart(json.sla.by_priority);
    renderAtRiskTable(json.sla.at_risk_cases);
  } catch (err) {
    console.error("Error loading analytics:", err);
  }
}

function updateKPICards(data) {
  // Turnaround overall
  const tatMean = data.turnaround.overall.mean;
  document.getElementById("kpi-avg-tat").textContent = `${tatMean}m`;

  // SLA breach rate
  const breachRate = data.sla.overall_breach_rate_pct;
  document.getElementById("kpi-breach-rate").textContent = `${breachRate}%`;

  // At risk cases
  const atRiskCount = data.sla.at_risk_count;
  document.getElementById("kpi-at-risk").textContent = atRiskCount;

  // Pending count
  const pendingTotal = Object.values(data.pending_priority).reduce((a, b) => a + b, 0);
  document.getElementById("kpi-pending-total").textContent = pendingTotal;

  // Total completed
  document.getElementById("kpi-completed-total").textContent = data.sla.total_completed;
}

function renderWorkloadChart(workloads) {
  const ctx = document.getElementById("chart-workload");
  if (!ctx) return;

  const labels = workloads.map(w => w.name.replace("Dr. ", ""));
  const activeCases = workloads.map(w => w.active_cases);
  const maxCapacities = workloads.map(w => w.max_capacity);

  if (workloadChartInstance) workloadChartInstance.destroy();

  workloadChartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Active Assigned Cases",
          data: activeCases,
          backgroundColor: "#3b82f6",
          borderRadius: 4,
        },
        {
          label: "Max Capacity",
          data: maxCapacities,
          backgroundColor: "rgba(148, 163, 184, 0.2)",
          borderRadius: 4,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "top" }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { stepSize: 1 },
          grid: { color: "rgba(255, 255, 255, 0.05)" }
        },
        x: {
          grid: { display: false }
        }
      }
    }
  });
}

function renderPendingPriorityChart(pending) {
  const ctx = document.getElementById("chart-pending-priority");
  if (!ctx) return;

  if (pendingPriorityChartInstance) pendingPriorityChartInstance.destroy();

  pendingPriorityChartInstance = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Critical", "High", "Routine"],
      datasets: [{
        data: [pending.Critical || 0, pending.High || 0, pending.Routine || 0],
        backgroundColor: ["#ef4444", "#f97316", "#10b981"],
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "68%",
      plugins: {
        legend: { position: "bottom" }
      }
    }
  });
}

function renderThroughputChart(throughput) {
  const ctx = document.getElementById("chart-throughput");
  if (!ctx) return;

  const labels = throughput.map(t => t.date.slice(5)); // MM-DD
  const counts = throughput.map(t => t.count);

  if (throughputChartInstance) throughputChartInstance.destroy();

  throughputChartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        label: "Reports Completed",
        data: counts,
        borderColor: "#ec4899",
        backgroundColor: "rgba(236, 72, 153, 0.12)",
        fill: true,
        tension: 0.35,
        pointRadius: 4,
        pointHoverRadius: 6,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { stepSize: 2 },
          grid: { color: "rgba(255, 255, 255, 0.05)" }
        },
        x: {
          grid: { display: false }
        }
      }
    }
  });
}

function renderSLABreachChart(byPriority) {
  const ctx = document.getElementById("chart-sla-breach");
  if (!ctx) return;

  const labels = ["Critical (60m)", "High (240m)", "Routine (1440m)"];
  const breachRates = [
    byPriority.Critical?.breach_rate_pct || 0,
    byPriority.High?.breach_rate_pct || 0,
    byPriority.Routine?.breach_rate_pct || 0,
  ];

  if (slaBreachChartInstance) slaBreachChartInstance.destroy();

  slaBreachChartInstance = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "Breach Rate (%)",
        data: breachRates,
        backgroundColor: ["#ef4444", "#f97316", "#10b981"],
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          ticks: { callback: v => v + "%" },
          grid: { color: "rgba(255, 255, 255, 0.05)" }
        },
        x: {
          grid: { display: false }
        }
      }
    }
  });
}

function renderAtRiskTable(cases) {
  const tbody = document.getElementById("at-risk-body");
  if (!tbody) return;

  if (!cases || cases.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align: center; color: var(--color-routine); padding: 1.5rem;">
          All active cases are currently well within SLA targets. No cases at risk.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = cases.map(c => `
    <tr>
      <td style="font-family: var(--font-mono); font-weight: 600;">${c.case_id}</td>
      <td style="font-family: var(--font-mono); color: var(--accent-cyan);">${c.patient_ref}</td>
      <td>
        <span class="badge ${c.priority_label === 'Critical' ? 'badge-critical' : c.priority_label === 'High' ? 'badge-high' : 'badge-routine'}">
          ${c.priority_label}
        </span>
      </td>
      <td>${c.rad_name}</td>
      <td style="font-family: var(--font-mono);">${c.waiting_minutes}m</td>
      <td>
        <span class="badge ${c.is_breached ? 'badge-critical' : 'badge-atrisk'}">
          ${c.is_breached ? 'EXPIRED' : c.remaining_minutes + 'm left'}
        </span>
      </td>
      <td>
        <a href="/case/${c.case_id}" class="btn btn-outline btn-sm">Inspect &rarr;</a>
      </td>
    </tr>
  `).join("");
}

document.addEventListener("DOMContentLoaded", () => {
  loadHospitalAnalytics();
  setInterval(loadHospitalAnalytics, 30000); // 30-sec refresh
});
