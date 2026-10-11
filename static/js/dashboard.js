/* ==========================================================================
   EDUSORT DASHBOARD & COMPLAINT FORM JS UTILITIES (dashboard.js)
   ========================================================================== */

document.addEventListener('DOMContentLoaded', function() {
    // Live preview for student complaint form
    const complaintTextarea = document.getElementById('complaint-text-input');
    const previewBox = document.getElementById('live-preview-box');
    const previewCategory = document.getElementById('preview-category');
    
    if (complaintTextarea && previewBox) {
        complaintTextarea.addEventListener('input', function() {
            const text = complaintTextarea.value.trim();
            if (text.length > 5) {
                previewBox.style.display = 'block';
                
                // Anti-ragging triage suggestion preview (triage aid only)
                const criticalWords = ["hospital", "bleeding", "weapon", "knife", "suicide", "locked in", "unconscious", "emergency"];
                const highWords = ["beat", "slap", "punch", "extort", "money", "blackmail", "forced to", "threatened"];
                
                let isCrit = criticalWords.some(w => text.toLowerCase().includes(w));
                let isHigh = highWords.some(w => text.toLowerCase().includes(w));
                
                if (isCrit) {
                    previewCategory.textContent = "Provisional Triage: Critical Priority (Immediate Committee Review)";
                    previewCategory.className = "badge badge-escalated";
                } else if (isHigh) {
                    previewCategory.textContent = "Provisional Triage: High Priority (Expedited ARC Review)";
                    previewCategory.className = "badge badge-assigned";
                } else {
                    previewCategory.textContent = "Provisional Triage: Standard Priority (Confidential ARC Queue)";
                    previewCategory.className = "badge badge-open";
                }
            } else {
                previewBox.style.display = 'none';
            }
        });
    }

    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.opacity = '0';
            alert.style.transform = 'translateY(-10px)';
            alert.style.transition = 'opacity 0.5s, transform 0.5s';
            setTimeout(() => alert.remove(), 500);
        }, 5000);
    });

    // Custom single-character focus jumping for verify OTP boxes if implemented
    const otpFields = document.querySelectorAll('.otp-digit');
    if (otpFields.length > 0) {
        otpFields.forEach((field, index) => {
            field.addEventListener('input', (e) => {
                if (e.target.value.length === 1 && index < otpFields.length - 1) {
                    otpFields[index + 1].focus();
                }
                combineOtp();
            });

            field.addEventListener('keydown', (e) => {
                if (e.key === 'Backspace' && index > 0 && e.target.value.length === 0) {
                    otpFields[index - 1].focus();
                }
            });
        });

        function combineOtp() {
            let combined = '';
            otpFields.forEach(field => combined += field.value);
            const hiddenOtpInput = document.getElementById('hidden-otp-input');
            if (hiddenOtpInput) {
                hiddenOtpInput.value = combined;
            }
        }
    }
});
