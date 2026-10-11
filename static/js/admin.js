/* ==========================================================================
   ANTI-RAGGING COMMITTEE (ARC) ADMIN JS UTILITIES (admin.js)
   ========================================================================== */

function openComplaintModal(data) {
    document.getElementById('modal-ticket-id').textContent = '#' + data.id;
    document.getElementById('modal-form-id').value = data.id;
    document.getElementById('modal-email').textContent = data.student_email;
    document.getElementById('modal-text').textContent = data.complaint_text;
    document.getElementById('modal-category').textContent = data.incident_type || 'Other / unsure';
    document.getElementById('modal-datetime').textContent = data.incident_date_time || 'N/A';
    document.getElementById('modal-location').textContent = data.incident_location || 'N/A';
    document.getElementById('modal-accused').textContent = data.accused_details || 'None specified';
    document.getElementById('modal-witnesses').textContent = data.witness_details || 'None specified';
    document.getElementById('modal-committee').textContent = data.committee_assigned || 'Anti-Ragging Squad';
    document.getElementById('modal-timestamp').textContent = data.timestamp;

    // Severity badge
    const sevBadge = document.getElementById('modal-severity');
    if (sevBadge) {
        const sev = data.severity_level || 'Medium';
        sevBadge.textContent = sev;
        sevBadge.className = 'prio-badge prio-' + sev.toLowerCase().replace(/\s+/g, '-');
    }

    // Confidentiality Notice
    const confWarning = document.getElementById('modal-confidential-warning');
    const stdNotice = document.getElementById('modal-standard-notice');
    if (data.is_confidential) {
        if (confWarning) confWarning.style.display = 'block';
        if (stdNotice) stdNotice.style.display = 'none';
    } else {
        if (confWarning) confWarning.style.display = 'none';
        if (stdNotice) stdNotice.style.display = 'block';
    }

    // Status select
    const select = document.getElementById('modal-status-select');
    if (select) {
        let statusVal = data.status;
        if (statusVal === 'In Progress' || statusVal === 'In-Progress' || statusVal === 'Assigned') {
            statusVal = 'Under Review';
        } else if (statusVal === 'Resolved') {
            statusVal = 'Closed';
        }
        select.value = statusVal;
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
    const categoryFilter = document.getElementById('admin-filter-category');
    const tableRows = document.querySelectorAll('.complaint-row');

    function filterTable() {
        const query = searchInput ? searchInput.value.toLowerCase().trim() : '';
        const statusVal = statusFilter ? statusFilter.value.toLowerCase().trim() : '';
        const catVal = categoryFilter ? categoryFilter.value.toLowerCase().trim() : '';

        tableRows.forEach(row => {
            const text = row.textContent.toLowerCase();
            const status = (row.getAttribute('data-status') || '').toLowerCase();
            const category = (row.getAttribute('data-category') || '').toLowerCase();

            const matchesSearch = query === '' || text.includes(query);
            
            let matchesStatus = true;
            if (statusVal !== '') {
                if (statusVal === 'under review') {
                    matchesStatus = status.includes('review') || status.includes('progress') || status.includes('assigned');
                } else if (statusVal === 'closed') {
                    matchesStatus = status.includes('closed') || status.includes('resolved');
                } else {
                    matchesStatus = status === statusVal;
                }
            }

            const matchesCategory = catVal === '' || category.includes(catVal);

            if (matchesSearch && matchesStatus && matchesCategory) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        });
    }

    if (searchInput) searchInput.addEventListener('input', filterTable);
    if (statusFilter) statusFilter.addEventListener('change', filterTable);
    if (categoryFilter) categoryFilter.addEventListener('change', filterTable);

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
