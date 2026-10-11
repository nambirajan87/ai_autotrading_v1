import streamlit as st
from app.auth.auth_manager import authenticate_user, register_user


def render_auth_page():
    """Renders the secure Login and Sign Up screen for the unified trading platform."""
    st.markdown("""
    <style>
        .auth-container {
            max-width: 480px;
            margin: 40px auto;
            padding: 32px 28px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 16px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
        }
        .auth-title {
            text-align: center;
            font-size: 1.8rem;
            font-weight: 800;
            color: #00e676;
            margin-bottom: 6px;
        }
        .auth-subtitle {
            text-align: center;
            font-size: 0.9rem;
            color: #bdbdbd;
            margin-bottom: 24px;
        }
        .auth-badge {
            display: inline-block;
            background: rgba(0, 230, 118, 0.15);
            color: #00e676;
            padding: 3px 10px;
            border-radius: 10px;
            font-size: 0.75rem;
            font-weight: 600;
            border: 1px solid #00e676;
            margin-bottom: 12px;
        }
    </style>
    """, unsafe_allow_html=True)

    col_l, col_center, col_r = st.columns([1, 2, 1])

    with col_center:
        st.markdown("""
        <div style="text-align: center;">
            <div class="auth-badge">🛡️ SECURE ENCRYPTED PORTAL</div>
            <h2 class="auth-title">AI Auto-Trading Suite</h2>
            <p class="auth-subtitle">Unified Live Execution • Paper Simulation • Backtest Lab</p>
        </div>
        """, unsafe_allow_html=True)

        tab_login, tab_signup = st.tabs(["🔑 Sign In", "📝 Create New Account"])

        with tab_login:
            st.markdown("##### Enter Credentials")
            with st.form("login_form", clear_on_submit=False):
                login_user = st.text_input("Username", placeholder="e.g. admin or trader1", key="login_username_input")
                login_pass = st.text_input("Password", type="password", placeholder="••••••••", key="login_password_input")
                btn_login = st.form_submit_button("🚀 Sign In to Workspace", type="primary", use_container_width=True)

                if btn_login:
                    success, msg, user_data = authenticate_user(login_user, login_pass)
                    if success:
                        st.session_state.authenticated = True
                        st.session_state.current_user = user_data
                        st.success(f"Welcome back, {user_data.get('full_name', login_user)}!")
                        st.rerun()
                    else:
                        st.error(msg)

            st.markdown("---")
            st.caption("⚡ **Default Credentials (Local Setup):** `admin` / `admin123`")
            if st.button("⚡ Quick One-Click Demo Login (Admin)", use_container_width=True):
                success, msg, user_data = authenticate_user("admin", "admin123")
                if success:
                    st.session_state.authenticated = True
                    st.session_state.current_user = user_data
                    st.rerun()
                else:
                    st.error(msg)

        with tab_signup:
            st.markdown("##### Register New Trader Account")
            with st.form("signup_form", clear_on_submit=False):
                reg_name = st.text_input("Full Name", placeholder="e.g. John Doe", key="reg_fullname_input")
                reg_user = st.text_input("Choose Username", placeholder="e.g. jdoe_trader", key="reg_username_input")
                reg_email = st.text_input("Email (Optional)", placeholder="john@example.com", key="reg_email_input")
                reg_pass = st.text_input("Password (min 6 chars)", type="password", placeholder="••••••••", key="reg_password_input")
                reg_confirm = st.text_input("Confirm Password", type="password", placeholder="••••••••", key="reg_confirm_password_input")
                btn_reg = st.form_submit_button("📝 Create Account", type="primary", use_container_width=True)

                if btn_reg:
                    if reg_pass != reg_confirm:
                        st.error("Passwords do not match. Please verify and re-enter.")
                    else:
                        success, msg = register_user(
                            username=reg_user,
                            password=reg_pass,
                            full_name=reg_name,
                            email=reg_email,
                        )
                        if success:
                            st.success(f"{msg} You can now switch to the 'Sign In' tab.")
                        else:
                            st.error(msg)

        st.caption("🔒 All credentials are salted and hashed using PBKDF2-HMAC-SHA256 (100,000 iterations) and stored in local `data/users.db`.")
