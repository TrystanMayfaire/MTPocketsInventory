import re
from datetime import datetime, date
import dash
from dash import html, dcc, Input, Output, State, callback, callback_context, no_update
import dash_bootstrap_components as dbc
import dash_ag_grid as dag
from sqlalchemy import select, or_
from sqlalchemy.orm import aliased
import secrets

# Import models, ENGINE, and SessionLocal from your database.py
from database import SessionLocal, Category, Prop, PropImage, PropCheckout, User, InviteCode
from constants import ERA_PERIOD_OPTIONS, CONDITION_OPTIONS, USER_LEVEL_OPTIONS


app = dash.Dash(
    __name__,
    requests_pathname_prefix='/inventory/',
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True
)
app.title = "MT Pockets Theatre Inventory Catalog"
server = app.server

@server.after_request
def allow_iframe(response):
    response.headers["X-Frame-Options"] = "ALLOWALL"
    return response


# --- HELPER FUNCTIONS ---
EMAIL_REGEX = r"^[\w\.-]+@[\w\.-]+\.\w+$"
USERNAME_REGEX = r"^[a-zA-Z0-9_.-]{3,30}$"

def validate_username(username: str) -> str | None:
    """Validates username length and character constraints."""
    if not username or not username.strip():
        return "⚠️ Username is required."
    clean_un = username.strip()
    if len(clean_un) < 3 or len(clean_un) > 30:
        return "⚠️ Must be between 3 and 30 characters."
    if not re.match(USERNAME_REGEX, clean_un):
        return "⚠️ Only letters, numbers, underscores, hyphens, and dots allowed."
    return None

def validate_password(password: str, username: str = "") -> str | None:
    """Validates password strength rules."""
    if not password:
        return "⚠️ Password is required."
    if len(password) < 8:
        return "⚠️ Password must be at least 8 characters long."
    if len(password) > 128:
        return "⚠️ Password cannot exceed 128 characters."
    if not re.search(r"[A-Z]", password):
        return "⚠️ Must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "⚠️ Must contain at least one lowercase letter."
    if not re.search(r"\d", password):
        return "⚠️ Must contain at least one number."
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        return "⚠️ Must contain at least one special character."
    if username and username.strip().lower() in password.lower():
        return "⚠️ Password cannot contain your username."
    return None

def get_top_categories():
    """Fetch all top-level categories (parent_id IS NULL)."""
    session = SessionLocal()
    try:
        stmt = select(Category).where(Category.parent_id.is_(None)).order_by(Category.name)
        return [{"label": c.name, "value": c.id} for c in session.scalars(stmt).all()]
    finally:
        session.close()


def get_all_child_category_ids(parent_category_id: int):
    """
    Recursively fetch a category ID and all its nested child category IDs
    so selecting 'Hand Props' returns items in any subcategory of 'Hand Props'.
    """
    session = SessionLocal()
    try:
        top_cat = aliased(Category)
        cte = select(top_cat.id).where(top_cat.id == parent_category_id).cte(name="subcats", recursive=True)

        child_cat = aliased(Category)
        cte = cte.union_all(select(child_cat.id).where(child_cat.parent_id == cte.c.id))
        return list(session.execute(select(cte.c.id)).scalars().all())
    finally:
        session.close()


def build_full_category_path(category_id: int):
    """Generate 'Department > Subcategory > Specific Type' string for grid display."""
    if not category_id:
        return "Unassigned"

    session = SessionLocal()
    try:
        path = []
        curr = session.get(Category, category_id)
        while curr:
            path.append(curr.name)
            curr = session.get(Category, curr.parent_id) if curr.parent_id else None
        path.reverse()
        return " > ".join(path)
    finally:
        session.close()


# --- TABLE COLUMNS ---

column_defs = [
    {"field": "id", "headerName": "ID", "width": 70, "sortable": True, "filter": True},
    {"field": "name", "headerName": "Item Name", "minwidth": 180, "flex": 2,
              "sortable": True, "filter": True, "hide": False},
    {"field": "category_path", "headerName": "Category Path", "flex": 2, "minwidth": 160,
              "sortable": True, "filter": True},
    {"field": "storage_location", "headerName": "Location", "flex": 1, "sortable": True, "filter": True},
    {"field": "era_period", "headerName": "Era / Period", "width": 150, "sortable": True, "filter": True},
    {"field": "condition", "headerName": "Condition", "width": 120, "sortable": True, "filter": True},
    {"field": "status", "headerName": "Status", "width": 130, "sortable": True, "filter": True,
        "cellStyle": {
            "styleConditions": [
                {"condition": "params.value == 'Available'", "style": {"color": "#28a745", "fontWeight": "bold"}},
                {"condition": "params.value == 'Checked Out'", "style": {"color": "#dc3545", "fontWeight": "bold"}},
                {"condition": "params.value == 'In Maintenance'", "style": {"color": "#ffc107", "fontWeight": "bold"}}
            ]
        }
    },
    {"field": "created_on_str", "headerName": "Date Added", "width": 120, "sortable": True, "filter": True}
]

# --- APP LAYOUT ---

column_picker = dbc.Accordion(
    [
        dbc.AccordionItem(
            dbc.Checklist(
                id="column-toggle-checklist",
                options=[
                    {"label": "ID", "value": "id"},
                    {"label": "Category Path", "value": "category_path"},
                    {"label": "Location", "value": "storage_location"},
                    {"label": "Era / Period", "value": "era_period"},
                    {"label": "Condition", "value": "condition"},
                    {"label": "Status", "value": "status"},
                    {"label": "Date Added", "value": "created_on_str"},
                ],
                # Default checked columns:
                value=["id", "category_path", "location", "condition", "status"],
                inline=True,
                switch=True,
            ),
            title="⚙️ Customize Visible Columns",
        )
    ],
    start_collapsed=True,
    className="mb-3",
)

app.layout = dbc.Container([
    dcc.Store(id="user-auth-store", data={"logged_in": False, "username": ""}),
    dcc.Store(id="refresh-trigger-store", data=0),
    dcc.Store(id="active-prop-id-store", data=None),

    dbc.Button("+",
               id="open-add-modal-btn",
               className="fab-add-btn fab-hidden shadow-lg",
               n_clicks=0),

    # Header
    dbc.Row([
        dbc.Col([
            html.Div([
                html.Div([
                    html.H1("MT Pockets Theatre", className="display-5 text-white fw-bold mb-0"),
                    html.P("Inventory Catalog & Production Asset Tracker", className="lead text-light mb-0")
                ]),
                html.Div([
                    html.Span(id="user-greeting-badge", className="me-3 text-light fw-bold"),
                    dbc.Button("User Login", id="toggle-login-btn", color="warning", outline=True, size="sm", className="me-2"),
                    dbc.Button("Admin Panel", id="toggle-admin-btn", className="btn-mtp-secondary fw-bold px-3", style={'display': 'none'}),
                ], className="d-flex align-items-center mt-2 mt-md-0")
            ], className="d-flex flex-column flex-md-row align-items-start align-items-md-center justify-content-between")
        ])
    ], className="mtp-header rounded-3 shadow-sm"),

    # Filter Bar
    dbc.Row([
        # Category Selector (Level 1: Department)
        dbc.Col([
            dbc.Label("Department / Category", className="fw-bold"),
            dcc.Dropdown(id="dept-dropdown", options=get_top_categories(), placeholder="All Departments...", clearable=True)
        ], xs=12, md=3, className="mb-2 mb-md-0"),

        # Category Selector (Level 2: Subcategory)
        dbc.Col([
            dbc.Label("Subcategory", className="fw-bold"),
            dcc.Dropdown(id="subcat-dropdown", options=[], placeholder="Select Department First...", disabled=True, clearable=True)
        ], xs=12, md=3, className="mb-2 mb-md-0"),

        # Category Selector (Level 3: Specific Type)
        dbc.Col([
            dbc.Label("Specific Type", className="fw-bold"),
            dcc.Dropdown(id="leafcat-dropdown", options=[], placeholder="Select Subcategory First...", disabled=True, clearable=True)
        ], xs=12, md=3, className="mb-2 mb-md-0"),

        # Global Search Box
        dbc.Col([
            dbc.Label("Search All Items", className="fw-bold text-danger"),
            dbc.Input(id="search-input", type="text", placeholder="Search name, location, notes...", debounce=True)
        ], xs=12, md=3)
    ], className="mb-4 p-3 bg-white rounded shadow-sm"),

    # Grid Section
    dbc.Row([
        dbc.Col([
            dbc.Card([
                dbc.CardHeader([
                    html.Div([
                        html.H5("Catalog Inventory", className="mb-0 fw-bold"),
                        html.Span(id="item-count-badge", className="badge bg-secondary fs-6 ms-2"),
                        html.Small("(Click any item row to view details, checkout, or manage)",
                                   className="text-muted ms-auto d-none d-md-inline")
                    ], className="d-flex align-items-center")
                ]),
                dbc.CardBody([
                    column_picker,
                    dag.AgGrid(
                        id="inventory-grid",
                        columnDefs=column_defs,
                        rowData=[],
                        defaultColDef={"resizable": True, "sortable": True, "filter": True},
                        dashGridOptions={"pagination": True,
                                         "paginationPageSize": 15,
                                         "rowSelection": "single",
                        },
                        style={"height": "500px", "width": "100%"},
                        className="ag-theme-alpine"
                    )
                ])
            ])
        ])
    ]),

    # Modal 1: User Login
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Staff & Admin Sign In")),
        dbc.ModalBody([
            dbc.Label("Username"),
            dbc.Input(id="login-username-input", type="text", placeholder="Enter username...", className="mb-3"),
            dbc.Label("Password"),
            dbc.Input(id="login-password-input", type="password", placeholder="Enter password...", className="mb-3"),
            html.Div(id="login-error-msg", className="text-danger small"),
            html.Div([
            "Have an invite code? ",
            html.A("Sign Up Here", id="open-signup-modal-btn", href="#", className="text-primary text-decoration-underline fw-bold")
        ], className="small text-center mt-3")
        ]),
        dbc.ModalFooter([
            dbc.Button("Cancel", id="close-login-modal-btn", color="secondary", outline=True),
            dbc.Button("Sign In", id="submit-login-btn", color="primary")
        ])
    ], id="login-modal", is_open=False),

    # Modal 2: Add New Item
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Add New Inventory Item")),
        dbc.ModalBody([
            dbc.Form([
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Item Name *", className="fw-bold"),
                        dbc.Input(id="modal-name-input", type="text", placeholder="e.g., Antique Rotary Phone")
                    ], md=12, className="mb-3")
                ]),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Department", className="fw-bold"),
                        dcc.Dropdown(id="modal-dept-dropdown", options=get_top_categories(), placeholder="Select...")
                    ], md=4),
                    dbc.Col([
                        dbc.Label("Subcategory", className="fw-bold"),
                        dcc.Dropdown(id="modal-subcat-dropdown", options=[], disabled=True, placeholder="Select...")
                    ], md=4),
                    dbc.Col([
                        dbc.Label("Specific Type", className="fw-bold"),
                        dcc.Dropdown(id="modal-leafcat-dropdown", options=[], disabled=True, placeholder="Select...")
                    ], md=4)
                ], className="mb-3"),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Storage Location", className="fw-bold"),
                        dbc.Input(id="modal-location-input", type="text", placeholder="e.g., Shelf B-4, Rack 2")
                    ], md=4),
                    dbc.Col([
                        dbc.Label("Era / Period", className="fw-bold"),
                        dcc.Dropdown(id="modal-era-dropdown", options=ERA_PERIOD_OPTIONS, placeholder="Select Era...")
                    ], md=4),
                    dbc.Col([
                        dbc.Label("Current Condition", className="fw-bold"),
                        dcc.Dropdown(id="modal-condition-dropdown", options=CONDITION_OPTIONS, value="Good")
                    ], md=4)
                ], className="mb-3"),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Upload Photo(s)", className="fw-bold"),
                        dcc.Upload(
                            id="modal-image-upload",
                            children=html.Div(["Drag and Drop or ", html.A("Select Image File")]),
                            style={
                                "width": "100%", "height": "60px", "lineHeight": "60px",
                                "borderWidth": "1px", "borderStyle": "dashed", "borderRadius": "5px",
                                "textAlign": "center", "backgroundColor": "#fafafa"
                            },
                            multiple=False
                        ),
                        html.Div(id="modal-image-preview", className="mt-2 text-success small")
                    ], md=12, className="mb-3")
                ]),
                dbc.Row([
                    dbc.Col([
                        dbc.Label("Notes / Description", className="fw-bold"),
                        dbc.Textarea(id="modal-notes-input", placeholder="Detail color, material, special rules...")
                    ], md=12)
                ])
            ])
        ]),
        dbc.ModalFooter([
            dbc.Button("Cancel", id="close-add-modal-btn", color="secondary", outline=True),
            dbc.Button("Save Item", id="save-prop-btn", color="success", className="ms-2")
        ])
    ], id="add-item-modal", is_open=False, size="lg"),

    # Modal 3: Detailed View, Checkout, Check-In, and Delete
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle(id="detail-modal-title")),
        dbc.ModalBody([
            # Dynamic Info Container (Image, Item Meta Details)
            html.Div(id="detail-info-container"),

            # Static Auth Warning Banner
            dbc.Alert(
                "Sign in to check out, check in, or edit this item.",
                id="checkout-auth-alert",
                color="info",
                className="mt-3",
                style={"display": "none"}
            ),

            # Static Checkout Card
            dbc.Card([
                dbc.CardHeader("Check Out Item", className="fw-bold text-white bg-primary py-2"),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Borrower Name *", className="small fw-bold"),
                            dbc.Input(id="checkout-borrower-input", type="text", placeholder="e.g. Jane Doe")
                        ], md=4),
                        dbc.Col([
                            dbc.Label("Production Name", className="small fw-bold"),
                            dbc.Input(id="checkout-prod-input", type="text", placeholder="e.g. The Crucible")
                        ], md=4),
                        dbc.Col([
                            dbc.Label("Expected Return Date", className="small fw-bold"),
                            dcc.DatePickerSingle(id="checkout-date-input", date=date.today(), className="w-100")
                        ], md=4)
                    ], className="mb-2"),
                    dbc.Button("Complete Checkout", id="submit-checkout-btn", color="success", size="sm", className="mt-2")
                ])
            ], id="checkout-card", className="mt-3", style={"display": "none"}),

            # Static Check-In Card
            dbc.Card([
                dbc.CardHeader("Check In Item (Return)", className="fw-bold text-white bg-danger py-2"),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            dbc.Label("Returned Condition", className="small fw-bold"),
                            dcc.Dropdown(id="checkin-condition-dropdown", options=CONDITION_OPTIONS)
                        ], md=6),
                        dbc.Col([
                            dbc.Button("Process Return / Check In", id="submit-checkin-btn", color="warning", size="sm", className="mt-4")
                        ], md=6)
                    ])
                ])
            ], id="checkin-card", className="mt-3", style={"display": "none"}),

            html.Hr(),
            html.H6("Usage History Log", className="fw-bold"),
            html.Div(id="detail-history-container")
        ]),
        dbc.ModalFooter([
            dbc.Button("Delete Item", id="request-delete-btn", color="danger", outline=True, className="me-auto"),
            dbc.Button("Close", id="close-detail-modal-btn", color="secondary")
        ])
    ], id="detail-modal", is_open=False, size="lg"),

    # Modal 4: Secondary Delete Confirmation
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Confirm Item Deletion", className="text-danger")),
        dbc.ModalBody([
            html.P("Are you sure you want to permanently delete this item from the inventory?"),
            html.P("This action cannot be undone and will remove all associated images and checkout history logs.", className="text-muted small")
        ]),
        dbc.ModalFooter([
            dbc.Button("Cancel / Keep Item", id="cancel-delete-btn", color="secondary", outline=True),
            dbc.Button("Yes, Permanently Delete", id="confirm-delete-btn", color="danger")
        ])
    ], id="delete-confirm-modal", is_open=False),

    #Modal 5: Admin Panel
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("Admin Panel")),
        dbc.ModalBody([
            html.Div([
                html.Hr(),
                html.H6("🔑 Admin: Generate Staff Invite Code", className="fw-bold text-primary mb-3"),
                dbc.Row([
                    dbc.Col([
                        html.Div("Recipient Email", className="fw-bold small mb-1"),
                        dbc.Input(id="invite-email-input", type="email", placeholder="email@example.com", size="sm")
                    ], md=5),
                    dbc.Col([
                        html.Div("Assigned Role", className="fw-bold small mb-1"),
                        dcc.Dropdown(
                            id="invite-role-dropdown",
                            options=[
                                {"label": "Staff / Member", "value": "staff"},
                                {"label": "Administrator", "value": "admin"}
                            ],
                            value="staff",
                            clearable=False,
                            className="sm"
                        )
                    ], md=4),
                    dbc.Col([
                        html.Div(" Action", className="fw-bold small mb-1 text-white"), # Alignment spacer
                        dbc.Button("Generate Code", id="generate-code-btn", color="primary", size="sm", className="w-100")
                    ], md=3),
                ], className="g-2 mb-2 align-items-end"),
                html.Div(id="generated-code-output", className="small fw-bold text-success mt-2")
            ], id="admin-invite-section")
        ]),
        dbc.ModalFooter([
            dbc.Button("Cancel", id="close-admin-modal-btn", color="secondary", outline=True),
        ])
    ], id="admin-modal", is_open=False),

    # Modal 6 - User Registration / Sign Up
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("User Registration")),
        dbc.ModalBody([
            dbc.Label("Invite Code", className="fw-bold"),
            dbc.Input(id="signup-code-input", type="text", placeholder="XXXX-XXXX", className="mb-3"),
            html.Div(id="signup-code-error-msg", className="small mt-2"),

            dbc.Label("Choose Username", className="fw-bold"),
            dbc.Input(id="signup-username-input", type="text", placeholder="Choose username...", className="mb-3"),
            html.Div("3–30 characters (letters, numbers, '.', '_', '-')", className="text-muted extra-small mt-1"),
            html.Div(id="signup-username-error-msg", className="small mt-2"),

            dbc.Label("Choose Password", className="fw-bold"),
            dbc.Input(id="signup-password-input", type="password", placeholder="Choose password...", className="mb-3"),
            html.Div("8+ characters with uppercase, lowercase, number, & special character.", className="text-muted extra-small mt-1"),
            html.Div(id="signup-password-error-msg", className="small mt-2"),

            dbc.Label("Verify Password", className="fw-bold"),
            dbc.Input(id="signup-password-input2", type="password", placeholder="Retype password...", className="mb-3"),
            html.Div(id="signup-password2-error-msg", className="small mt-2"),


            dbc.Label("Name", className="fw-bold"),
            dbc.Input(id="signup-name-input", type="text", placeholder="FirstName LastName", className="mb-3"),
            html.Div(id="signup-name-error-msg", className="small mt-2"),

            dbc.Label("Email Address", className="fw-bold"),
            dbc.Input(id="signup-email-input", type="email", placeholder="your.email@example.com", className="mb-3"),
            html.Div(id="signup-email-error-msg", className="small mt-2"),

            dbc.Label("Verify Email Address", className="fw-bold"),
            dbc.Input(id="signup-email-input2", type="email", placeholder="your.email@example.com", className="mb-3"),
            html.Div(id="signup-email2-error-msg", className="small mt-2"),

        ]),
        dbc.ModalFooter([
            dbc.Button("Cancel", id="close-signup-modal-btn", color="secondary", outline=True),
            dbc.Button("Register", id="submit-signup-btn", color="success")
        ])
    ], id="signup-modal", is_open=False),

], fluid=True, className="p-2 p-md-4")


# --- CALLBACKS ---

# Main Inventory Filter
@callback(
    Output("inventory-grid", "rowData"),
    Output("item-count-badge", "children"),
    Input("dept-dropdown", "value"),
    Input("subcat-dropdown", "value"),
    Input("leafcat-dropdown", "value"),
    Input("search-input", "value"),
    Input("refresh-trigger-store", "data")
)
def filter_inventory(dept_id, subcat_id, leafcat_id, search_term, refresh_trigger):
    session = SessionLocal()
    try:
        stmt = select(Prop)

        # Apply category filter
        active_cat_id = leafcat_id or subcat_id or dept_id
        if active_cat_id:
            category_ids = get_all_child_category_ids(active_cat_id)
            stmt = stmt.where(Prop.category_id.in_(category_ids))

        if search_term and search_term.strip():
            term = f"%{search_term.strip()}%"
            stmt = stmt.where(
                or_(
                    Prop.name.ilike(term),
                    Prop.storage_location.ilike(term),
                    Prop.era_period.ilike(term),
                    Prop.notes.ilike(term)
                )
            )

        props = session.scalars(stmt.order_by(Prop.name)).all()

        row_data = []
        for p in props:
            row_data.append({
                "id": p.id,
                "name": p.name,
                "category_path": build_full_category_path(p.category_id),
                "storage_location": p.storage_location or "Unassigned",
                "era_period": p.era_period or "Unspecified",
                "condition": p.condition or "Good",
                "status": p.status,
                "created_on_str": p.created_on.strftime("%Y-%m-%d") if p.created_on else "N/A",
                "notes": p.notes or ""
            })

        return row_data, f"{len(row_data)} Items"
    finally:
        session.close()


# Loads subcategories when department is selected
@callback(
    Output("subcat-dropdown", "options"),
    Output("subcat-dropdown", "disabled"),
    Output("subcat-dropdown", "value"),
    Input("dept-dropdown", "value")
)
def update_subcategories(selected_dept_id):
    if not selected_dept_id:
        return [], True, None

    session = SessionLocal()
    try:
        stmt = select(Category).where(Category.parent_id == selected_dept_id).order_by(Category.name)
        return [{"label": c.name, "value": c.id} for c in session.scalars(stmt).all()], False, None
    finally:
        session.close()

# Loads sub-subcategories when subcategory is selected
@callback(
    Output("leafcat-dropdown", "options"),
    Output("leafcat-dropdown", "disabled"),
    Output("leafcat-dropdown", "value"),
    Input("subcat-dropdown", "value")
)
def update_leaf_categories(selected_subcat_id):
    if not selected_subcat_id:
        return [], True, None

    session = SessionLocal()
    try:
        stmt = select(Category).where(Category.parent_id == selected_subcat_id).order_by(Category.name)
        return [{"label": c.name, "value": c.id} for c in session.scalars(stmt).all()], False, None
    finally:
        session.close()

# Add item modal category cascades
@callback(
    Output("modal-subcat-dropdown", "options"),
    Output("modal-subcat-dropdown", "disabled"),
    Output("modal-subcat-dropdown", "value"),
    Input("modal-dept-dropdown", "value")
)
def update_modal_subcategories(selected_dept_id):
    if not selected_dept_id:
        return [], True, None

    session = SessionLocal()
    try:
        stmt = select(Category).where(Category.parent_id == selected_dept_id).order_by(Category.name)
        return [{"label": c.name, "value": c.id} for c in session.scalars(stmt).all()], False, None
    finally:
        session.close()


@callback(
    Output("modal-leafcat-dropdown", "options"),
    Output("modal-leafcat-dropdown", "disabled"),
    Output("modal-leafcat-dropdown", "value"),
    Input("modal-subcat-dropdown", "value")
)
def update_modal_leaf_categories(selected_subcat_id):
    if not selected_subcat_id:
        return [], True, None

    session = SessionLocal()
    try:
        stmt = select(Category).where(Category.parent_id == selected_subcat_id).order_by(Category.name)
        return [{"label": c.name, "value": c.id} for c in session.scalars(stmt).all()], False, None
    finally:
        session.close()


# User Login & Authentication State
@callback(
    Output("login-modal", "is_open"),
    Output("user-auth-store", "data"),
    Output("login-error-msg", "children"),
    Output("toggle-login-btn", "children"),
    Output("user-greeting-badge", "children"),
    Output('toggle-admin-btn', 'style'),
    Output("open-add-modal-btn", "className"),
    Input("toggle-login-btn", "n_clicks"),
    Input("submit-login-btn", "n_clicks"),
    Input("close-login-modal-btn", "n_clicks"),
    State("login-username-input", "value"),
    State("login-password-input", "value"),
    State("user-auth-store", "data"),
    prevent_initial_call=True
)
def handle_user_login(toggle_clicks, submit_clicks, close_clicks, username, password, auth_data):
    ctx = callback_context
    if not ctx.triggered:
        return False, auth_data, "", "User Login", "", {"display": "none"}, "fab-add-btn fab-hidden shadow-lg"

    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if btn_id == "toggle-login-btn":
        if auth_data.get("logged_in"):
            return False, {"logged_in": False, "username": ""}, "", "User Login", "", {"display": "none"}, "fab-add-btn fab-hidden shadow-lg"
        elif auth_data.get("role") == 'admin':
            return True, auth_data, "", "User Login", "", {"display": "block"}, "fab-add-btn shadow-lg"
        return True, auth_data, "", "User Login", "", {"display": "none"}, "fab-add-btn shadow-lg"

    if btn_id == "submit-login-btn":
        if not username or not password:
            return True, auth_data, "Please enter both username and password.", "User Login", "", {"display": "none"}, "fab-add-btn fab-hidden shadow-lg"

        session = SessionLocal()
        try:
            user = session.scalars(select(User).where(User.username == username.strip())).first()
            if user and user.check_password(password):
                new_auth = {"logged_in": True,
                            "username": user.username,
                            "full_name": user.full_name or user.username,
                            "role": user.role}
                role = str(new_auth["role"]).lower()
                if  role == "admin":
                    return False, new_auth, "", "Sign Out", f"Logged in: {user.full_name or user.username}", {"display": "inline-block"}, "fab-add-btn shadow-lg"
                return False, new_auth, "", "Sign Out", f"Logged in: {user.full_name or user.username}", {"display": "none"}, "fab-add-btn shadow-lg"
            return True, auth_data, "Invalid username or password.", "User Login", "", {"display": "none"}, "fab-add-btn fab-hidden shadow-lg"
        finally:
            session.close()

    if btn_id == "close-login-modal-btn":
        btn_label = "Sign Out" if auth_data.get("logged_in") else "User Login"
        greeting = f"Logged in: {auth_data.get('full_name')}" if auth_data.get("logged_in") else ""
        return False, auth_data, "", btn_label, greeting, {"display": "none"}, "fab-add-btn shadow-lg"

    return False, auth_data, "", "User Login", "", {"display": "none"}, "fab-add-btn fab-hidden shadow-lg"


# 5. Add Item Modal Toggle
@callback(
    Output("add-item-modal", "is_open"),
    Input("open-add-modal-btn", "n_clicks"),
    Input("close-add-modal-btn", "n_clicks"),
    State("user-auth-store", "data"),
    prevent_initial_call=True
)
def toggle_add_item_modal(open_clicks, close_clicks, auth_data):
    ctx = callback_context
    if not ctx.triggered:
        return False

    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if btn_id == "open-add-modal-btn":
        if auth_data.get("logged_in"):
            return True
        return False
    return False


# Image Preview
@callback(
    Output("modal-image-preview", "children"),
    Input("modal-image-upload", "filename")
)
def update_image_preview(filename):
    return f"Image selected: {filename}" if filename else ""


# Save New Item
@callback(
    Output("refresh-trigger-store", "data", allow_duplicate=True),
    Output("modal-name-input", "value"),
    Output("modal-dept-dropdown", "value"),
    Output("modal-location-input", "value"),
    Output("modal-era-dropdown", "value"),
    Output("modal-condition-dropdown", "value"),
    Output("modal-notes-input", "value"),
    Input("save-prop-btn", "n_clicks"),
    State("modal-name-input", "value"),
    State("modal-dept-dropdown", "value"),
    State("modal-subcat-dropdown", "value"),
    State("modal-leafcat-dropdown", "value"),
    State("modal-location-input", "value"),
    State("modal-era-dropdown", "value"),
    State("modal-condition-dropdown", "value"),
    State("modal-image-upload", "contents"),
    State("modal-notes-input", "value"),
    State("refresh-trigger-store", "data"),
    prevent_initial_call=True
)
def save_new_item(n_clicks, name, dept_id, subcat_id, leafcat_id, location, era, condition, image_contents, notes, refresh_count):
    if not name or not name.strip():
        return refresh_count, name, dept_id, location, era, condition, notes

    selected_cat_id = leafcat_id or subcat_id or dept_id

    session = SessionLocal()
    try:
        new_prop = Prop(
            name=name.strip(),
            category_id=selected_cat_id,
            storage_location=location.strip() if location else None,
            era_period=era,
            condition=condition or "Good",
            notes=notes.strip() if notes else None,
            status="Available",
            created_on=datetime.now()
        )
        session.add(new_prop)
        session.flush()

        if image_contents:
            img = PropImage(prop_id=new_prop.id, image_path=image_contents, is_primary=True)
            session.add(img)

        session.commit()
    finally:
        session.close()

    return refresh_count + 1, "", None, "", None, "Good", ""


# Detail View, Checkout, & Check-In Callback (Cell Click Architecture)
@callback(
    Output("detail-modal", "is_open"),
    Output("detail-modal-title", "children"),
    Output("detail-info-container", "children"),
    Output("detail-history-container", "children"),
    Output("checkout-auth-alert", "style"),
    Output("checkout-card", "style"),
    Output("checkin-card", "style"),
    Output("checkin-condition-dropdown", "value"),
    Output("checkout-borrower-input", "value"),
    Output("checkout-prod-input", "value"),
    Output("active-prop-id-store", "data"),
    Output("request-delete-btn", "style"),
    Output("refresh-trigger-store", "data", allow_duplicate=True),
    Input("inventory-grid", "cellClicked"),  # <-- Swapped from selectedRows
    Input("close-detail-modal-btn", "n_clicks"),
    Input("submit-checkout-btn", "n_clicks"),
    Input("submit-checkin-btn", "n_clicks"),
    State("user-auth-store", "data"),
    State("active-prop-id-store", "data"),
    State("checkout-borrower-input", "value"),
    State("checkout-prod-input", "value"),
    State("checkout-date-input", "date"),
    State("checkin-condition-dropdown", "value"),
    State("inventory-grid", "virtualRowData"),
    prevent_initial_call=True
)
def manage_item_detail(cell_clicked, close_clicks, checkout_clicks, checkin_clicks,
                       auth_data, active_prop_id, borrower, prod_name, exp_return_date,
                       checkin_condition, virtual_row_data):
    ctx = callback_context
    if not ctx.triggered:
        return False, "", "", "", {"display": "none"}, {"display": "none"}, {"display": "none"}, None, "", "", None, {"display": "none"}, dash.no_update

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    # Close Modal Action
    if trigger_id == "close-detail-modal-btn":
        return False, "", "", "", {"display": "none"}, {"display": "none"}, {"display": "none"}, None, "", "", None, {"display": "none"}, dash.no_update

    # Process Checkout Action
    if trigger_id == "submit-checkout-btn" and active_prop_id and borrower:
        session = SessionLocal()
        try:
            prop = session.get(Prop, active_prop_id)
            if prop:
                prop.status = "Checked Out"
                exp_date = datetime.strptime(exp_return_date, "%Y-%m-%d") if exp_return_date else None
                checkout_rec = PropCheckout(
                    prop_id=prop.id,
                    borrower_name=borrower.strip(),
                    production_name=prod_name.strip() if prod_name else None,
                    checked_out_on=datetime.now(),
                    expected_return_date=exp_date
                )
                session.add(checkout_rec)
                session.commit()
        finally:
            session.close()

    # Process Check-In Action
    if trigger_id == "submit-checkin-btn" and active_prop_id:
        session = SessionLocal()
        try:
            prop = session.get(Prop, active_prop_id)
            if prop:
                prop.status = "Available"
                if checkin_condition:
                    prop.condition = checkin_condition

                active_checkout = session.scalars(
                    select(PropCheckout).where(PropCheckout.prop_id == active_prop_id, PropCheckout.returned_on.is_(None))
                ).first()
                if active_checkout:
                    active_checkout.returned_on = datetime.now()
                    active_checkout.return_condition = checkin_condition

                session.commit()
        finally:
            session.close()

    # Determine Active Prop ID
    prop_id = active_prop_id
    if trigger_id == "inventory-grid" and cell_clicked:
        if isinstance(cell_clicked, dict):
            if "rowIndex" in cell_clicked and virtual_row_data:
                row_idx = cell_clicked["rowIndex"]
                if 0 <= row_idx < len(virtual_row_data):
                    prop_id = virtual_row_data[row_idx]["id"]

    if not prop_id:
        return False, "", "", "", {"display": "none"}, {"display": "none"}, {"display": "none"}, None, "", "", None, {"display": "none"}, dash.no_update

    # Fetch Prop & Build Modal Components
    session = SessionLocal()
    try:
        prop = session.get(Prop, prop_id)
        if not prop:
            return False, "", "", "", {"display": "none"}, {"display": "none"}, {"display": "none"}, None, "", "", None, {"display": "none"}, dash.no_update

        primary_img = session.scalars(
            select(PropImage).where(PropImage.prop_id == prop_id, PropImage.is_primary.is_(True))
        ).first()

        img_elem = html.Div("No Photo Available", className="p-4 bg-light text-center text-muted rounded")
        if primary_img:
            img_elem = html.Img(src=primary_img.image_path, style={"maxWidth": "100%", "maxHeight": "280px", "borderRadius": "8px"})

        info_content = dbc.Row([
            dbc.Col([img_elem], md=5, className="text-center mb-3 mb-md-0"),
            dbc.Col([
                html.H4(prop.name, className="fw-bold text-primary"),
                html.P([html.Strong("Category: "), build_full_category_path(prop.category_id)]),
                html.P([html.Strong("Location: "), prop.storage_location or "Unassigned"]),
                html.P([html.Strong("Era / Period: "), prop.era_period or "Unspecified"]),
                html.P([html.Strong("Condition: "), prop.condition or "Good"]),
                html.P([html.Strong("Status: "), prop.status]),
                html.P([html.Strong("Notes: "), prop.notes or "None"])
            ], md=7)
        ])

        # Action Panel Styles
        is_logged_in = auth_data.get("logged_in", False)
        delete_btn_style = {"display": "inline-block"} if is_logged_in else {"display": "none"}

        alert_style = {"display": "none"}
        checkout_style = {"display": "none"}
        checkin_style = {"display": "none"}

        if not is_logged_in:
            alert_style = {"display": "block"}
        elif prop.status == "Available":
            checkout_style = {"display": "block"}
        else:
            checkin_style = {"display": "block"}

        # Checkout History List
        history_records = session.scalars(
            select(PropCheckout).where(PropCheckout.prop_id == prop_id).order_by(PropCheckout.checked_out_on.desc())
        ).all()

        history_items = []
        if history_records:
            for h in history_records:
                ret_str = h.returned_on.strftime("%Y-%m-%d") if h.returned_on else "Currently Out"
                history_items.append(html.Li(
                    f"{h.borrower_name} ({h.production_name or 'General'}) — Checked Out: {h.checked_out_on.strftime('%Y-%m-%d')} | Returned: {ret_str}"
                ))
        else:
            history_items.append(html.Li("No checkout history logged yet.", className="text-muted"))

        history_content = html.Ul(history_items, className="small")

        # Reset Form Fields when loading/refreshing
        new_borrower = "" if trigger_id in ["submit-checkout-btn", "inventory-grid"] else borrower
        new_prod = "" if trigger_id in ["submit-checkout-btn", "inventory-grid"] else prod_name

        return (
            True,
            f"Item Details (ID #{prop.id})",
            info_content,
            history_content,
            alert_style,
            checkout_style,
            checkin_style,
            prop.condition,
            new_borrower,
            new_prod,
            prop_id,
            delete_btn_style,
            datetime.now()
        )
    finally:
        session.close()


# Item Deletion Confirmation Modal Handler
@callback(
    Output("delete-confirm-modal", "is_open"),
    Output("detail-modal", "is_open", allow_duplicate=True),
    Output("refresh-trigger-store", "data", allow_duplicate=True),
    Input("request-delete-btn", "n_clicks"),
    Input("cancel-delete-btn", "n_clicks"),
    Input("confirm-delete-btn", "n_clicks"),
    State("active-prop-id-store", "data"),
    State("refresh-trigger-store", "data"),
    prevent_initial_call=True
)
def handle_deletion_flow(request_clicks, cancel_clicks, confirm_clicks, active_prop_id, refresh_count):
    ctx = callback_context
    if not ctx.triggered:
        return False, True, refresh_count

    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if btn_id == "request-delete-btn":
        return True, True, refresh_count

    if btn_id == "cancel-delete-btn":
        return False, True, refresh_count

    if btn_id == "confirm-delete-btn" and active_prop_id:
        session = SessionLocal()
        try:
            prop = session.get(Prop, active_prop_id)
            if prop:
                session.delete(prop)
                session.commit()
        finally:
            session.close()
        return False, False, refresh_count + 1

    return False, True, refresh_count

@app.callback(
    Output("inventory-grid", "columnDefs"),
    Input("column-toggle-checklist", "value")
)
def update_column_visibility(visible_columns):
    updated_defs = []
    for col in column_defs:
        col_copy = col.copy()
        # Item Name stays always visible; other columns toggle based on checklist
        if col_copy["field"] != "name":
            col_copy["hide"] = col_copy["field"] not in visible_columns
        updated_defs.append(col_copy)
    return updated_defs

@callback(
    Output("admin-modal", "is_open"),
    Output("generated-code-output", "children"),
    Input("toggle-admin-btn", "n_clicks"),
    Input("close-admin-modal-btn", "n_clicks"),
    Input("generate-code-btn", "n_clicks"),
    State("invite-email-input", "value"),
    State("invite-role-dropdown", "value"),
    State("user-auth-store", "data"),
    prevent_initial_call=True
)
def admin_panel_control(admin_n_clicks, close_admin_n_clicks, gen_code_n_clicks, email, role, auth_data):
    ctx = callback_context
    if not ctx.triggered:
        return False, ""

    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if btn_id == "toggle-admin-btn":
        return True, ""

    elif btn_id == "close-admin-modal-btn":
        return False, ""

    elif btn_id == "generate-code-btn":

        if not auth_data.get("logged_in") or not email or auth_data.get("role").lower() != "admin":
            return True, "⚠️ Please login to an admin account to generate an invite code."

        if not email or not email.strip():
            return True, "⚠️ Please enter a recipient email address."

        # Logic to create entry in InviteCode table & return generated token string
        clean_email = email.strip().lower()
        session = SessionLocal()
        try:
            # Check if code already exists for email
            existing = session.query(InviteCode).filter_by(email=clean_email).first()
            if existing:
                status_str = "Used" if existing.is_used else "Active / Unused"
                return True, f"⚠️  Code already exists for {clean_email}: '{existing.code}' (Used: {status_str})"

            raw_token = secrets.token_hex(4).upper()
            formatted_code = f"{raw_token[:4]}-{raw_token[4:]}"

            new_invite = InviteCode(
                code=formatted_code,
                email=clean_email,
                assigned_role=role or "user",
                is_used=False
            )
            session.add(new_invite)
            session.commit()

            return True, f"✅ Code generated for {clean_email}: {formatted_code} (Role: {role})"
        except Exception as e:
            session.rollback()
            return True, f"❌ Error generating code: {str(e)}"
        finally:
            session.close()
    return False, ""

@callback(
    Output("signup-modal", "is_open"),
    Output("login-modal", "is_open", allow_duplicate=True),
    Input("open-signup-modal-btn", "n_clicks"),
    Input("close-signup-modal-btn", "n_clicks"),
    State("signup-modal", "is_open"),
    prevent_initial_call=True
)
def toggle_signup_modal(open_clicks, close_clicks, is_open):
    ctx = dash.callback_context
    if not ctx.triggered:
        return is_open, False

    button_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if button_id == "open-signup-modal-btn":
        return True, False  # Open signup modal, close login modal
    elif button_id == "close-signup-modal-btn":
        return False, True # Close signup modal

    return is_open, False

@callback(
    Output("signup-modal", "is_open", allow_duplicate=True),
    Output("login-modal", "is_open", allow_duplicate=True),
    Output("login-username-input", "value"),
    Output("login-password-input", "value"),
    Output("signup-code-error-msg", "children"),
    Output("signup-code-error-msg", "className"),
    Output("signup-username-error-msg", "children"),
    Output("signup-username-error-msg", "className"),
    Output("signup-password-error-msg", "children"),
    Output("signup-password-error-msg", "className"),
    Output("signup-password2-error-msg", "children"),
    Output("signup-password2-error-msg", "className"),
    Output("signup-name-error-msg", "children"),
    Output("signup-name-error-msg", "className"),
    Output("signup-email-error-msg", "children"),
    Output("signup-email-error-msg", "className"),
    Output("signup-email2-error-msg", "children"),
    Output("signup-email2-error-msg", "className"),
    Input("submit-signup-btn", "n_clicks"),
    State("signup-code-input", "value"),
    State("signup-username-input", "value"),
    State("signup-password-input", "value"),
    State("signup-password-input2", "value"),
    State("signup-name-input", "value"),
    State("signup-email-input", "value"),
    State("signup-email-input2", "value"),
    prevent_initial_call=True
)
def handle_user_registration(submit_clicks, invite_code, username, password,
                             password2, full_name, email, email2):
    if not submit_clicks:
        return (
            dash.no_update, dash.no_update, dash.no_update, dash.no_update,
            "", "small", "", "small", "", "small", "", "small", "", "small", "", "small", "", "small"
        )

    # Initialize error containers for all 6 fields
    code_err, un_err, pw_err, pw2_err, name_err, em1_err, em2_err = "", "", "", "", "", "", ""
    has_error = False

    # 1. Field Required Checks
    if not invite_code or not invite_code.strip():
        code_err = "⚠️ Invite code is required."
        has_error = True

    un_validation = validate_username(username)
    if un_validation:
        un_err = un_validation
        has_error = True

    pw_validation = validate_password(password, username=username)
    if pw_validation:
        pw_err = pw_validation
        has_error = True

    if not password2:
        pw2_err = "⚠️ Please confirm your password."
        has_error = True
    elif password and password != password2:
        pw2_err = "⚠️ Passwords do not match."
        has_error = True

    if not full_name or not full_name.strip():
        name_err = "⚠️ Full name is required."
        has_error = True

    if not email or not email.strip():
        em1_err = "⚠️ Email address is required."
        has_error = True

    if not email2 or not email2.strip():
        em2_err = "⚠️ Please verify your email address."
        has_error = True

    # 2. Email Matching Validation
    clean_email = email.strip().lower() if email else ""
    clean_email2 = email2.strip().lower() if email2 else ""

    if clean_email and not re.match(EMAIL_REGEX, clean_email):
        em1_err = "⚠️ Please enter a valid email address (e.g., name@example.com)."
        has_error = True

    if clean_email and clean_email2 and clean_email != clean_email2:
        em2_err = "⚠️ Email addresses do not match."
        has_error = True

    if has_error:
        err_cls = "small text-danger mt-1"
        return (
            True, False, dash.no_update, dash.no_update,
            code_err, err_cls if code_err else "small",
            un_err, err_cls if un_err else "small",
            pw_err, err_cls if pw_err else "small",
            pw2_err, err_cls if pw2_err else "small",
            name_err, err_cls if name_err else "small",
            em1_err, err_cls if em1_err else "small",
            em2_err, err_cls if em2_err else "small"
        )

    raw_code = re.sub(r"[^A-Za-z0-9]", "", invite_code).upper()

    # Standard code structure is 8 characters long with hyphen (e.g., XXXX-YYYY)
    if len(raw_code) == 8:
        clean_code = f"{raw_code[:4]}-{raw_code[4:]}"
    else:
        clean_code = invite_code.strip().upper()

    clean_username = username.strip()

    session = SessionLocal()
    try:
        # 3. Database Validation: Invite Code Checks
        invite = session.scalars(select(InviteCode).where(InviteCode.code == clean_code)).first()
        if not invite:
            code_err = "❌ Invalid invite code."
            has_error = True
        elif invite.is_used:
            code_err = "❌ This invite code has already been used."
            has_error = True
        elif invite.email.strip().lower() != clean_email:
            em1_err = "❌ Email address does not match invite record."
            has_error = True

        # 4. Database Validation: Username Check
        existing_user = session.scalars(select(User).where(User.username == clean_username)).first()
        if existing_user:
            un_err = "⚠️ Username is already taken."
            has_error = True

        existing_email = session.scalars(select(User).where(User.email == clean_email)).first()
        if existing_email:
            em1_err = "⚠️ A user with this email address already exists."

        if has_error:
            err_cls = "small text-danger mt-1"
            return (
                True, False, dash.no_update, dash.no_update,
                code_err, err_cls if code_err else "small",
                un_err, err_cls if un_err else "small",
                pw_err, err_cls if pw_err else "small",
                pw2_err, err_cls if pw2_err else "small",
                name_err, err_cls if name_err else "small",
                em1_err, err_cls if em1_err else "small",
                em2_err, err_cls if em2_err else "small"
            )

        # 5. Create New User & Mark Invite Code Used
        new_user = User(
            username=clean_username,
            full_name=full_name.strip(),
            role=invite.assigned_role or "user",
            email=clean_email,
        )
        new_user.set_password(password)

        invite.is_used = True

        session.add(new_user)
        session.commit()

        # Success: Close signup modal, pre-fill username on login modal, clear all errors
        return (
            False, True, clean_username, "",
            "", "small", "", "small", "", "small", "", "small",
            "", "small", "", "small", "", "small"
        )

    except Exception as e:
        session.rollback()
        return (
            True, False, dash.no_update, dash.no_update,
            f"❌ Registration failed: {str(e)}", "small text-danger mt-1",
            "", "small", "", "small", "", "small", "", "small", "", "small", "", "small"
        )
    finally:
        session.close()

if __name__ == "__main__":
    app.run(debug=True, port=8050)