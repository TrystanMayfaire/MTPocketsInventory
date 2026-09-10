"""
seed_data.py - Standalone Script to Populate Sample Data into inventory.db
Run this script whenever you want to populate test items without altering database.py.
"""

from datetime import datetime
from sqlalchemy import select
from database import SessionLocal, init_db, Category, Prop, User


def seed_sample_data():
    # 1. Ensure tables and category trees exist
    init_db()

    session = SessionLocal()
    try:
        # 2. Check if props already exist to avoid duplicate seeding
        existing_count = session.query(Prop).count()
        if existing_count > 0:
            print(f"Database already contains {existing_count} items. Skipping seed.")
            return

        print("Seeding sample inventory items...")

        # 3. Find target category IDs (or default to None if not matched)
        def get_category_id(keyword):
            cat = session.scalars(
                select(Category).where(Category.name.ilike(f"%{keyword}%"))
            ).first()
            return cat.id if cat else None

        props_cat_id = get_category_id("Prop") or get_category_id("Hand")
        furniture_cat_id = get_category_id("Furniture") or get_category_id("Chair")
        costume_cat_id = get_category_id("Costume") or get_category_id("Dress")

        # 4. Define sample props
        sample_props = [
            Prop(
                name="Vintage Black Rotary Phone",
                category_id=props_cat_id,
                storage_location="Shelf B-4",
                era_period="1950s",
                condition="Good",
                status="Available",
                notes="Classic desk rotary phone with heavy base and cloth cord. Non-functional bell.",
                created_on=datetime.now()
            ),
            Prop(
                name="Victorian Velvet Armchair",
                category_id=furniture_cat_id,
                storage_location="Stage Left Storage",
                era_period="Victorian",
                condition="Fair",
                status="Available",
                notes="Dark mahogany frame with maroon velvet upholstery. Minor wear on right armrest.",
                created_on=datetime.now()
            ),
            Prop(
                name="Brass Oil Lantern",
                category_id=props_cat_id,
                storage_location="Cabinet C, Shelf 1",
                era_period="19th Century",
                condition="Good",
                status="Available",
                notes="Polished brass decorative lantern with glass globe.",
                created_on=datetime.now()
            ),
            Prop(
                name="1920s Flapper Feather Fan",
                category_id=costume_cat_id,
                storage_location="Costume Bin #12",
                era_period="1920s",
                condition="Excellent",
                status="Available",
                notes="Ostrich feather hand fan with faux pearl handle.",
                created_on=datetime.now()
            )
        ]

        session.add_all(sample_props)
        session.commit()
        print(f"Successfully added {len(sample_props)} sample inventory items!")

    except Exception as e:
        session.rollback()
        print(f"Error seeding database: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    seed_sample_data()