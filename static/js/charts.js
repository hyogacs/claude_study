/* チャート関連のユーティリティ */

const CHART_COLORS = [
    '#0d6efd', '#198754', '#dc3545', '#ffc107', '#0dcaf0',
    '#6f42c1', '#fd7e14', '#20c997', '#d63384', '#6c757d',
];

function formatYenShort(val) {
    if (val >= 100000000) return (val / 100000000).toFixed(1) + '億';
    if (val >= 10000) return (val / 10000).toFixed(0) + '万';
    return '¥' + Math.round(val).toLocaleString();
}

function createPieChart(canvasId, labels, values, title) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: CHART_COLORS.slice(0, labels.length),
                borderWidth: 2,
                borderColor: '#fff',
            }],
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 15,
                        font: { size: 11 },
                    },
                },
                tooltip: {
                    callbacks: {
                        label: function(ctx) {
                            const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                            const pct = ((ctx.raw / total) * 100).toFixed(1);
                            return ctx.label + ': ' + formatYenShort(ctx.raw) + ' (' + pct + '%)';
                        },
                    },
                },
            },
        },
    });
}
