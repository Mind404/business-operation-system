// 业务高效运转系统 - 前端交互脚本
document.addEventListener('DOMContentLoaded', function() {
    // 自动隐藏提示消息
    const alerts = document.querySelectorAll('.alert-auto');
    alerts.forEach(function(alert) {
        setTimeout(function() {
            alert.style.opacity = '0';
            setTimeout(function() { alert.remove(); }, 300);
        }, 3000);
    });

    // 表格行点击高亮
    const tableRows = document.querySelectorAll('.data-table tbody tr');
    tableRows.forEach(function(row) {
        row.addEventListener('click', function(e) {
            if (e.target.tagName === 'BUTTON' || e.target.tagName === 'A' || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') {
                return;
            }
        });
    });

    // 数字输入框最小值限制
    const numberInputs = document.querySelectorAll('input[type="number"][min]');
    numberInputs.forEach(function(input) {
        input.addEventListener('blur', function() {
            const min = parseFloat(this.getAttribute('min'));
            if (this.value !== '' && parseFloat(this.value) < min) {
                this.value = min;
            }
        });
    });

    // 表单提交前确认（有data-confirm属性的表单）
    const confirmForms = document.querySelectorAll('form[data-confirm]');
    confirmForms.forEach(function(form) {
        form.addEventListener('submit', function(e) {
            if (!confirm(this.getAttribute('data-confirm'))) {
                e.preventDefault();
            }
        });
    });
});

// 通用工具函数
function formatDate(dateStr) {
    if (!dateStr) return '-';
    return dateStr.substring(0, 10);
}

function formatDateTime(dateStr) {
    if (!dateStr) return '-';
    return dateStr.substring(0, 16);
}

// 页面加载时间统计（调试用）
window.addEventListener('load', function() {
    const perf = performance.getEntriesByType('navigation')[0];
    if (perf && console) {
        console.log('页面加载完成，耗时:', Math.round(perf.loadEventEnd - perf.startTime), 'ms');
    }
});
