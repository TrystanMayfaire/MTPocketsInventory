import os
import json
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "inventory.db")
JSON_PATH = os.path.join(BASE_DIR, "categories.json")

ENGINE = create_engine(f"sqlite:///{DB_PATH}", echo=False)

Base = declarative_base()
SessionLocal = sessionmaker(bind=ENGINE)

class InviteCode(Base):
    __tablename__ = 'invite_codes'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(16), unique=True, nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    assigned_role = Column(String(50), default="User")
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.now)

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(80), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(120), nullable=True)
    role = Column(String(50), default='user')
    email = Column(String(120), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Category(Base):
    __tablename__ = 'categories'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    parent_id = Column(Integer, ForeignKey('categories.id', ondelete='CASCADE'), nullable=True)

    subcategories = relationship("Category", backref="parent", remote_side=[id])
    props = relationship("Prop", back_populates="category")


class Prop(Base):
    __tablename__ = 'props'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), nullable=False)
    category_id = Column(Integer, ForeignKey('categories.id', ondelete='SET NULL'), nullable=True)
    storage_location = Column(String(100))
    era_period = Column(String(50))
    condition = Column(String(50), default='Good')
    status = Column(String(30), default='Available')  # 'Available', 'Checked Out', 'In Maintenance'
    notes = Column(Text, nullable=True)
    created_on = Column(DateTime, default=datetime.now)  # Automatic timestamp on item creation

    category = (relationship("Category", back_populates="category_props")
                if hasattr(Category, 'category_props') else relationship("Category", back_populates="props"))
    images = relationship("PropImage", back_populates="prop", cascade="all, delete-orphan")
    checkouts = relationship("PropCheckout", back_populates="prop", cascade="all, delete-orphan")


class PropImage(Base):
    __tablename__ = 'prop_images'

    id = Column(Integer, primary_key=True, autoincrement=True)
    prop_id = Column(Integer, ForeignKey('props.id', ondelete='CASCADE'), nullable=False)
    image_path = Column(Text, nullable=False)
    is_primary = Column(Boolean, default=False)
    caption = Column(String(255), nullable=True)

    prop = relationship("Prop", back_populates="images")


class PropCheckout(Base):
    __tablename__ = 'prop_checkouts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    prop_id = Column(Integer, ForeignKey('props.id', ondelete='CASCADE'), nullable=False)
    borrower_name = Column(String(150), nullable=False)
    production_name = Column(String(150), nullable=True)
    checked_out_on = Column(DateTime, default=datetime.now)
    expected_return_date = Column(DateTime, nullable=True)
    returned_on = Column(DateTime, nullable=True)
    return_condition = Column(String(50), nullable=True)

    prop = relationship("Prop", back_populates="checkouts")


def insert_category_tree(session, category_nodes, parent_id=None):
    """Recursively walks through nested JSON category nodes and inserts into SQLite."""
    for node in category_nodes:
        cat = Category(name=node["name"], parent_id=parent_id)
        session.add(cat)
        session.flush()

        if "children" in node and node["children"]:
            insert_category_tree(session, node["children"], parent_id=cat.id)


def init_db():
    """Initializes database tables and populates categories from categories.json if empty."""
    Base.metadata.create_all(ENGINE)

    session = SessionLocal()

    # Seed initial categories if empty
    if not session.query(Category).first():
        if os.path.exists(JSON_PATH):
            with open(JSON_PATH, "r", encoding="utf-8") as f:
                categories_data = json.load(f)
                insert_category_tree(session, categories_data)
                session.commit()
                print("Seeded category tree successfully.")

    # Seed default admin user if no users exist
    if not session.query(User).first():
        admin = User(username="admin", full_name="MT Pockets Admin", role="Admin", email="mtpocketstheater@gmail.com")
        admin.set_password("mtp123")  # Default password
        session.add(admin)
        session.commit()
        print("Created default user: admin / mtp123")

    session.close()


if __name__ == "__main__":
    init_db()
    print("Database ready at:", DB_PATH)