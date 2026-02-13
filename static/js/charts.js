/* Chart.js utilities - Apple style with percentage labels */

Chart.register(ChartDataLabels);

const CHART_COLORS = [
    '#007aff', '#34c759', '#ff9500', '#ff3b30', '#5ac8fa',
    '#af52de', '#ff2d55', '#ffcc00', '#64d2ff', '#86868b',
];

function formatYenShort(val) {
    if (val >= 100000000) return (val / 100000000).toFixed(1) + '億';
    if (val >= 10000) return (val / 10000).toFixed(0) + '万';
    return '¥' + Math.round(val).toLocaleString();
}

function createPieChart(canvasId, labels, values, title, names) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;

    const total = values.reduce((a, b) => a + b, 0);

    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: CHART_COLORS.slice(0, labels.length),
                borderWidth: 3,
                borderColor: '#ffffff',
                hoverBorderWidth: 0,
                hoverOffset: 6,
            }],
        },
        options: {
            responsive: true,
            cutout: '55%',
            layout: { padding: 30 },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(0,0,0,0.78)',
                    titleFont: { size: 12, weight: '600' },
                    bodyFont: { size: 12 },
                    cornerRadius: 10,
                    padding: 12,
                    callbacks: {
                        label: function(ctx) {
                            const pct = ((ctx.raw / total) * 100).toFixed(1);
                            const name = names ? names[ctx.dataIndex] : '';
                            const display = name ? ctx.label + ' ' + name : ctx.label;
                            return display + ': ' + formatYenShort(ctx.raw) + ' (' + pct + '%)';
                        },
                    },
                },
                datalabels: {
                    color: '#1d1d1f',
                    font: { size: 11, weight: '600', family: '-apple-system, sans-serif' },
                    anchor: 'end',
                    align: 'end',
                    offset: 4,
                    formatter: function(value, ctx) {
                        const pct = ((value / total) * 100).toFixed(1);
                        if (pct < 3) return '';
                        const label = ctx.chart.data.labels[ctx.dataIndex];
                        return label + '\n' + pct + '%';
                    },
                },
            },
        },
    });
}
