from datetime import datetime, date
import dash
from dash import html, dcc, Input, Output, State, callback, callback_context, no_update
import dash_bootstrap_components as dbc
import dash_ag_grid as dag
from sqlalchemy import select, or_
from sqlalchemy.orm import aliased

# Import models, ENGINE, and SessionLocal from your database.py
from database import SessionLocal, Category, Prop, PropImage, PropCheckout, User
from constants import ERA_PERIOD_OPTIONS, CONDITION_OPTIONS

app = dash.Dash(
    __name__,
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
                    dbc.Button("+ Add New Item", id="open-add-modal-btn", className="btn-mtp-primary fw-bold px-3")
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
            html.Div(id="login-error-msg", className="text-danger small")
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
    ], id="delete-confirm-modal", is_open=False)

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
        return False, auth_data, "", "User Login", ""

    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if btn_id == "toggle-login-btn":
        if auth_data.get("logged_in"):
            return False, {"logged_in": False, "username": ""}, "", "User Login", ""
        return True, auth_data, "", "User Login", ""

    if btn_id == "submit-login-btn":
        if not username or not password:
            return True, auth_data, "Please enter both username and password.", "User Login", ""

        session = SessionLocal()
        try:
            user = session.scalars(select(User).where(User.username == username.strip())).first()
            if user and user.check_password(password):
                new_auth = {"logged_in": True, "username": user.username, "full_name": user.full_name or user.username}
                return False, new_auth, "", "Sign Out", f"Logged in: {user.full_name or user.username}"
            return True, auth_data, "Invalid username or password.", "User Login", ""
        finally:
            session.close()

    if btn_id == "close-login-modal-btn":
        btn_label = "Sign Out" if auth_data.get("logged_in") else "User Login"
        greeting = f"Logged in: {auth_data.get('full_name')}" if auth_data.get("logged_in") else ""
        return False, auth_data, "", btn_label, greeting

    return False, auth_data, "", "User Login", ""


# 5. Add Item Modal Toggle
@callback(
    Output("add-item-modal", "is_open"),
    Output("login-modal", "is_open", allow_duplicate=True),
    Input("open-add-modal-btn", "n_clicks"),
    Input("close-add-modal-btn", "n_clicks"),
    State("user-auth-store", "data"),
    prevent_initial_call=True
)
def toggle_add_item_modal(open_clicks, close_clicks, auth_data):
    ctx = callback_context
    if not ctx.triggered:
        return False, False

    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if btn_id == "open-add-modal-btn":
        if auth_data.get("logged_in"):
            return True, False
        return False, True  # Open login modal if not authenticated

    return False, False


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


if __name__ == "__main__":
    app.run(debug=True, port=8050)