import streamlit as st
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import pdfplumber
import requests
import re
import io

# ==========================================
# 1. 보안 및 API 설정
# ==========================================
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
    GSHEET_SCRIPT_URL = st.secrets["GSHEET_SCRIPT_URL"]
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')
except:
    st.error("⚠️ Secrets 설정 정보를 확인해주세요.")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 인쇄 전용 CSS 및 가독성 최적화
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
            font-size: 9.5pt !important;
            line-height: 1.5 !important;
        }
        @page { margin: 1.5cm; }
    }
    .print-btn-style {
        background-color: #ff4b4b; color: white; padding: 10px 20px;
        border-radius: 8px; text-align: center; display: inline-block;
        font-weight: bold; cursor: pointer; border: none; width: 100%;
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 3. 데이터 가공 및 캐싱 (속도 향상)
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
                txt = str(row.iloc[0])
                g_m, d_m = re.search(r'(\d)학년', txt), re.search(r'\((\d{2})-(\d{2})\)', txt)
                if g_m and d_m:
                    eng = str(row.iloc[10]); eng_v = re.search(r'[1-9]', eng)
                    eng_s = 100 - (int(eng_v.group()) - 1) * 10 if eng_v else 0
                    m_res.append({"key": int(f"{d_m.group(1)}{d_m.group(2)}"), "시험": f"{g_m.group(1)}학년 {d_m.group(2)}월", "국어": float(row.iloc[4]), "수학": float(row.iloc[8]), "영어": eng_s, "탐구": float(row.iloc[13])})
            except: continue
        m_df = pd.DataFrame(m_res).sort_values("key").drop(columns="key") if m_res else pd.DataFrame()
    return i_df, m_df

# ==========================================
# 4. 메인 UI
# ==========================================
st.set_page_config(page_title="고3 대입 전문 컨설팅", layout="wide")
st.title("🎓 고3 대입 전문 컨설팅")

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
                extracted_text = f"\n[자료: {ref_file.name}]\n"
                if ref_file.name.endswith(".pdf"):
                    with pdfplumber.open(ref_file) as p: extracted_text += "".join([pg.extract_text() for pg in p.pages])
                else:
                    xls_ref = pd.ExcelFile(ref_file)
                    for s in xls_ref.sheet_names: extracted_text += f"\n--- {s} ---\n{pd.read_excel(xls_ref, s).to_string()}\n"
                sync_knowledge(extracted_text); st.success("동기화 완료!")

# ==========================================
# 5. 분석 로직 (논리적 일관성 강화)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('데이터 기반 보수적 정밀 분석 중...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_inst = "이 학생은 [농어촌 전형] 대상자이므로, 관련 지원 분석을 필수 포함할 것." if is_rural else ""
            
            prompt = f"""
            지방 일반고 컨설턴트로서 {target_major} 지망 학생 분석. 보수적 관점 유지.
            {rural_inst}
            [논리 지침] 
            1. 텍스트 진단 결과와 @PIE 태그 수치는 반드시 일치해야 함. 정시가 어렵다면 정시 비중은 10% 이하로 설정.
            2. 무조건 [PART 1: 종합 진단]으로 시작하며 제목은 생략함.

            [PART 1: 종합 진단] 성적 및 전형 적합성.
            [PART 2: 대입 전략] 대학 라인 및 추천 도서.
            [PART 3: 심화 탐구] 주제-근거-방법 순서 준수.
            [PART 4: 면접 대비] 질문-답안-준비 순서 준수.
            @PIE [교과: %, 정시: %, 종합: %] @
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:15000]}), 지식({k_base[:10000]})
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    res = st.session_state.analysis_result
    main_content = "[PART 1:" + res.split("[PART 1:")[1] if "[PART 1:" in res else res
    clean_res = re.sub(r'@.*?@', '', main_content, flags=re.DOTALL).strip()

    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 핵심 요약"])

    with tab1:
        st.subheader("📊 성적 및 전형 분석")
        c1, c2, c3 = st.columns(3)
        if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 추이"), use_container_width=True)
        if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이", range_y=[0, 100]), use_container_width=True)
        pie_raw = re.search(r'@PIE\s*\[(.*?)\]\s*@', res)
        if pie_raw:
            try:
                p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in [p.split(':') for p in pie_raw.group(1).split(',')]]
                c3.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="추천 전형"), use_container_width=True)
            except: pass
        st.markdown(clean_res.split("[PART 3:")[0].replace("[PART 1:", "### 📝 [PART 1]").replace("[PART 2:", "### 🎯 [PART 2]"))

    with tab2:
        if "[PART 3:" in clean_res:
            p3_area = clean_res.split("[PART 3:")[1].split("[PART 4:")[0]
            st.markdown("### 🚀 [PART 3] 심화 탐구 가이드")
            st.markdown(p3_area.replace("주제:", "#### 📍 주제:").replace("종적/횡적 근거:", "🔍 **종적/횡적 근거:**").replace("탐구 방법:", "🛠️ **탐구 방법:**"))
            if "[PART 4:" in clean_res:
                st.markdown("---")
                st.markdown("### 🎤 [PART 4] 면접 예상 질문")
                st.markdown(clean_res.split("[PART 4:")[1].replace("질문:", "#### ❓ 질문:").replace("모범 답안:", "✅ **모범 답안:**").replace("준비 방법:", "🛠️ **준비 방법:**"))

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 상담..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}")
                st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.subheader("🖨️ 인쇄용 리포트")
        st.markdown('<button class="print-btn-style" onclick="window.print()">📄 즉시 인쇄 또는 PDF 저장</button>', unsafe_allow_html=True)
        st.markdown("---")
        st.markdown(f'<div class="print-only"><h1 style="text-align: center;">대입 컨설팅 결과 리포트</h1><p style="text-align: right; font-weight: bold;">지원학과: {target_major}</p><hr><div style="white-space: pre-wrap;">{clean_res}</div></div>', unsafe_allow_html=True)
        st.markdown(clean_res)
