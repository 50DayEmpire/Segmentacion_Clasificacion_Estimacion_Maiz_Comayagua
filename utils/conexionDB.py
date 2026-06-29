import sqlite3
import streamlit as st
from config import GPKG_PATH

@st.cache_resource
def get_connection():
    return sqlite3.connect(GPKG_PATH, check_same_thread=False)

