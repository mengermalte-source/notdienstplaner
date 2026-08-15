from app.models.user import DoctorProfile


def test_doctor_profile_defaults():
    """Test that DoctorProfile has the new fields with correct defaults."""
    p = DoctorProfile(user_id=1)
    assert p.credit_factor == 1.0
    assert p.desired_shifts is None
    assert p.day_preference == "alle"
    assert p.sub_carried_over_score == 0.0
