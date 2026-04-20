import streamlit as st
import pandas as pd
import plotly.express as px
import pdfplumber
import requests
import re
import io
import urllib.parse
import google.generativeai as genai

# ==========================================
# 1. 보안 및 API 설정
# ==========================================
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    
    genai.configure(api_key=API_KEY)
    # 선생님 환경에서 성공한 최신 모델명 고정
    model = genai.GenerativeModel("gemini-2.5-flash") 
except Exception as e:
    st.error(f"⚠️ 시스템 설정 오류: {e}")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 인쇄 전용 CSS (어제 버전 그대로 복구)
# ==========================================
st.markdown("""
    <style>
    .print-only { display: none; }
    
    @media print {
        [data-testid="stSidebar"], header, footer, .stTabs, button, .stChatInput {
            display: none !important;
        }
        body * { visibility: hidden; }
        
        .print-only, .print-only * { 
            visibility: visible !important; 
        }
        .print-only {
            display: block !important;
            position: absolute; left: 0; top: 0;
            width: 100% !important;
            color: black !important;
            background-color: white !important;
            font-size: 10pt !important;
            line-height: 1.6 !important;
        }
        .print-only h1 { font-size: 18pt; text-align: center; border-bottom: 2px solid black; padding-bottom: 10px; margin-bottom: 15px;}
        .print-only h3 { font-size: 14pt; margin-top: 20px; color: #111; }
        @page { margin: 1.5cm; }
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 3. 데이터 가공 함수들
# ==========================================
def sync_knowledge(new_content=None):
    try:
        if new_content: requests.post(GSHEET_SCRIPT_URL, json={"content": new_content})
        response = requests.get(GSHEET_SCRIPT_URL)
        return response.text if response.status_code == 200 else ""
    except: return ""

@st.cache_data(show_spinner=False)
def process_performance_data(file_bytes):
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    i_df, m_df = pd.DataFrame(), pd.DataFrame()
    if '학생부현황' in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name='학생부현황')
        res = []
        for g in [1.0, 2.0, 3.0]:
            for s_idx, (u_c, r_c) in enumerate([(3, 5), (10, 12)], 1):
                sub = df[df.iloc[:, 0] == g]
                u_s, w_s = 0, 0
                for _, row in sub.iterrows():
                    try:
                        u, r = float(row.iloc[u_c]), str(row.iloc[r_c]).strip()
                        m = re.search(r'^[1-9]', r)
                        if m: u_s += u; w_s += (u * float(m.group()))
                    except: continue
                if u_s > 0: res.append({"학기": f"{int(g)}-{s_idx}", "등급": round(w_s/u_s, 2)})
        i_df = pd.DataFrame(res)
    if '수능모의고사' in xls.sheet_names:
        df_m = pd.read_excel(xls, sheet_name='수능모의고사')
        m_res = []
        for _, row in df_m.iterrows():
            try:
                txt = str(row.iloc[0]); g_m = re.search(r'(\d)학년', txt); d_m = re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    eng = str(row.iloc[10]); eng_v = re.search(r'[1-9]', eng); eng_s = 100 - (int(eng_v.group()) - 1) * 10 if eng_v else 0
                    m_res.append({"key": int(f"{d_m.group(1)}{d_m.group(2)}"), "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", "국어": float(row.iloc[4]), "수학": float(row.iloc[8]), "영어": eng_s, "탐구": float(row.iloc[13])})
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

def extract_section(text, start_keyword, end_keyword=None):
    if end_keyword: pattern = rf"\[{start_keyword}\].*?(?=\[{end_keyword}\]|$)"
    else: pattern = rf"\[{start_keyword}\].*"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(0).strip() if match else ""

# ==========================================
# 4. 메인 UI 구성
# ==========================================
st.set_page_config(page_title="고3 대입 전문 컨설팅", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅 시스템")

with st.sidebar:
    st.header("📋 학생 데이터 입력")
    target_major = st.text_input("희망 학과", placeholder="예: 신소재공학과")
    excel_file = st.file_uploader("1. 성적 엑셀", type=["xlsx"])
    pdf_file = st.file_uploader("2. 생기부 PDF", type="pdf")
    is_rural = st.checkbox("🌾 농어촌 전형 대상자 여부", value=False)
    st.divider()
    st.header("📚 지식 데이터베이스")
    ref_file = st.file_uploader("자료 업로드", type=["pdf", "xlsx"])
    if st.button("💾 영구 저장"):
        if ref_file:
            with st.spinner("저장 중..."):
                txt = f"\n[자료: {ref_file.name}]\n"
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: txt += "".join([pg.extract_text() for pg in p.pages])
                else:
                    xls_ref = pd.ExcelFile(ref_file)
                    for s in xls_ref.sheet_names: txt += f"\n- {s} -\n{pd.read_excel(xls_ref, s).to_string()}\n"
                sync_knowledge(txt); st.success("동기화 완료!")

# ==========================================
# 5. 분석 엔진 및 결과 UI
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('🚀 AI 데이터 분석 중...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_inst = "이 학생은 농어촌 전형 대상자이므로 대입 전략에 반영할 것." if is_rural else ""
            
            prompt = f"""
            지방 일반고 컨설턴트로서 {target_major} 지망 학생 분석. 음슴체 사용.
            {rural_inst}
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:12000]}), 지식({k_base[:5000]})
            [형식] PART 1: 진단, PART 2: 전략, PART 3: 탐구 주제, PART 4: 면접 질문 구성.
            @PIE [교과: 60, 정시: 10, 종합: 30] @
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # --- 대시보드 및 인쇄 리포트 구성 ---
    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL).strip()
    
    # 인쇄용 마크다운 병합
    final_report_markdown = f"""
### 📝 [PART 1] 종합 진단
{extract_section(clean_res, "PART 1", "PART 2")}

### 🎯 [PART 2] 대입 전략
{extract_section(clean_res, "PART 2", "PART 3")}

### 🚀 [PART 3] 심화 탐구 가이드
{extract_section(clean_res, "PART 3", "PART 4")}

### 🎤 [PART 4] 면접 예상 질문
{extract_section(clean_res, "PART 4")}
"""

    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 리포트 인쇄"])

    with tab1:
        st.subheader("📊 성적 분석 및 전형 추천")
        c1, c2, c3 = st.columns(3)
        if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 추이"))
        if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이"))
        
        pie_raw = re.search(r'@PIE\s*\[(.*?)\]\s*@', res)
        if pie_raw:
            p_items = [it.split(':') for it in pie_raw.group(1).split(',')]
            p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in p_items]
            c3.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="추천 전형"))
        
        st.markdown(extract_section(clean_res, "PART 1", "PART 2"))
        st.markdown(extract_section(clean_res, "PART 2", "PART 3"))

    with tab2:
        st.markdown(extract_section(clean_res, "PART 3", "PART 4"))
        st.divider()
        st.markdown(extract_section(clean_res, "PART 4"))

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("질문 입력..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}")
                st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.subheader("🖨️ 리포트 인쇄 및 저장")
        html_report = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <style>body{{font-family:'Malgun Gothic',sans-serif;padding:40px;line-height:1.6;}}h1{{text-align:center;border-bottom:2px solid #000;}}</style>
            </head><body onload="window.print()">
            <h1>대입 컨설팅 리포트 ({target_major})</h1>
            <div id="content"></div>
            <script>document.getElementById('content').innerHTML = marked.parse(decodeURIComponent("{urllib.parse.quote(final_report_markdown)}"));</script>
            </body></html>"""
        st.download_button("📄 리포트 PDF/인쇄 파일 받기", html_report, file_name=f"{target_major}_컨설팅.html", mime="text/html", use_container_width=True)
        st.markdown(f"<div class='print-only'>{final_report_markdown}</div>", unsafe_allow_html=True)
else:
    st.info("👈 왼쪽 사이드바에 정보를 입력하고 파일을 업로드해 주세요.")
