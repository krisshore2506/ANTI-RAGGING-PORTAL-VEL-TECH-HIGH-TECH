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
                
                // Simulate category check on keywords to update preview card
                const cleaningKeywords = ["dirty", "dust", "garbage", "smell", "toilet", "washroom", "bathroom", "clean", "waste", "mop", "sweep", "lab", "room"];
                const hostelKeywords = ["hostel", "room", "bed", "warden", "mess", "block"];
                
                let isCleaning = cleaningKeywords.some(word => text.toLowerCase().includes(word));
                let isHostel = hostelKeywords.some(word => text.toLowerCase().includes(word));
                
                if (isCleaning) {
                    if (isHostel) {
                        previewCategory.textContent = "Hostel - Cleaning (Auto-Assigned)";
                    } else {
                        previewCategory.textContent = "Campus - Cleaning (Auto-Assigned)";
                    }
                    previewCategory.className = "badge badge-assigned";
                } else {
                    previewCategory.textContent = "Academic / General Complaint (AI Routed)";
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
