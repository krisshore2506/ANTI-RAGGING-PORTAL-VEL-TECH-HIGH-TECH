from grievai_app import db, Cleaner, app

with app.app_context():

    cleaners = [

        # HOSTEL CLEANERS
        Cleaner(
            name="Ramesh",
            department="Hostel",
            area="Hostel_Room",
            phone="+91720032977"
        ),

        Cleaner(
            name="Suresh",
            department="Hostel",
            area="Hostel_Washroom",
            phone="+917200032977"
        ),

        Cleaner(
            name="Mahesh",
            department="Hostel",
            area="General_Cleaning",
            phone="+91720032977"
        ),


        # CAMPUS CLEANERS
        Cleaner(
            name="Kumar",
            department="Campus",
            area="Campus_Lab",
            phone="+917200032977"
        ),

        Cleaner(
            name="Arun",
            department="Campus",
            area="Campus_Classroom",
            phone="+917200032977"
        ),

        Cleaner(
            name="Vijay",
            department="Campus",
            area="Campus_Ground",
            phone="+91720032977"
        ),

        Cleaner(
            name="Ravi",
            department="Campus",
            area="General_Cleaning",
            phone="+91720032977"
        ),
    ]


    db.session.add_all(cleaners)
    db.session.commit()

    print("✅ General Cleaning Cleaners Added Successfully")
