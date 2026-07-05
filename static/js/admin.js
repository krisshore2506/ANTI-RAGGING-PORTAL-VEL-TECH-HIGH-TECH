/* ==========================================================================
   EDUSORT ADMIN JS UTILITIES (admin.js)
   ========================================================================== */

function openComplaintModal(data) {
    document.getElementById('modal-ticket-id').textContent = '#' + data.id;
    document.getElementById('modal-form-id').value = data.id;
    document.getElementById('modal-email').textContent = data.student_email;
    document.getElementById('modal-title').textContent = data.complaint_title || 'N/A';
    document.getElementById('modal-text').textContent = data.complaint_text;
    document.getElementById('modal-department').textContent = data.department;
    document.getElementById('modal-assigned').textContent = data.assigned_cleaner || 'None';
    document.getElementById('modal-priority').textContent = data.priority || 'Medium';
    document.getElementById('modal-timestamp').textContent = data.timestamp;
    
    // Status select
    const select = document.getElementById('modal-status-select');
    if (select) {
        select.value = data.status;
    }
    
    // Remarks
    const remarks = document.getElementById('modal-remarks-textarea');
    if (remarks) {
        remarks.value = data.admin_remarks || '';
    }
    
    // Show modal
    document.getElementById('complaint-modal').classList.add('active');
}

function closeComplaintModal() {
    document.getElementById('complaint-modal').classList.remove('active');
}

// Client-side search and filtering
document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.getElementById('admin-search');
    const statusFilter = document.getElementById('admin-filter-status');
    const deptFilter = document.getElementById('admin-filter-dept');
    const tableRows = document.querySelectorAll('.complaint-row');
    
    function filterTable() {
        const query = searchInput ? searchInput.value.toLowerCase() : '';
        const statusVal = statusFilter ? statusFilter.value.toLowerCase() : '';
        const deptVal = deptFilter ? deptFilter.value.toLowerCase() : '';
        
        tableRows.forEach(row => {
            const text = row.textContent.toLowerCase();
            const status = row.getAttribute('data-status').toLowerCase();
            const dept = row.getAttribute('data-dept').toLowerCase();
            
            const matchesSearch = text.includes(query);
            const matchesStatus = statusVal === '' || status === statusVal;
            const matchesDept = deptVal === '' || dept.includes(deptVal);
            
            if (matchesSearch && matchesStatus && matchesDept) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    }
    
    if (searchInput) searchInput.addEventListener('input', filterTable);
    if (statusFilter) statusFilter.addEventListener('change', filterTable);
    if (deptFilter) deptFilter.addEventListener('change', filterTable);
    
    // Close modal on clicking outside content area
    const modal = document.getElementById('complaint-modal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                closeComplaintModal();
            }
        });
    }
});
