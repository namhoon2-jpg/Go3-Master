import streamlit as st
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import pdfplumber
import requests
import re
import io
import urllib.parse

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
# 2. 인쇄 전용 CSS (Ctrl+P 및 다운로드용)
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
        .print-only h4 { font-size: 11pt; margin-top: 10px; color: #333; }
        .print-only p, .print-only li { font-size: 10pt; }
        @page { margin: 1.5cm; }
    }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 3. 데이터 가공 함수
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

# 안전 추출 함수
def extract_section(text, start_keyword, end_keyword=None):
    if end_keyword:
        pattern = rf"\[{start_keyword}\].*?(?=\[{end_keyword}\]|$)"
    else:
        pattern = rf"\[{start_keyword}\].*"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(0).strip() if match else ""

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
# 5. 분석 로직 (프롬프트 엄격 통제)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('데이터 기반 보수적 정밀 분석 중...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_inst = "이 학생은 [농어촌 전형] 대상자이므로 대입 전략에 포함할 것." if is_rural else ""
            
            # [수정] 면접 질문 제약 및 깔끔한 출력 유도
            prompt = f"""
            지방 일반고 컨설턴트로서 {target_major} 지망 학생 분석. 보수적 관점 유지.
            {rural_inst}
            
            [절대 지침]
            1. 모든 문장은 개괄식('-' 사용) 및 음슴체(~함, ~임) 사용.
            2. 정시 합격 확률이 낮으면 @PIE 태그 내 '정시' 비중을 반드시 10% 이하로 할 것.
            3. 면접 질문(PART 4)에는 농어촌 전형이나 학교 환경에 대한 질문을 절대 넣지 말 것. 오직 '지원 학과 관련 교과 세특 및 탐구 활동'에 관한 전공 적합성 질문만 3개 생성할 것.
            
            [PART 1]
            성적 및 전형 적합성
            [PART 2]
            대입 전략 및 추천 도서
            [PART 3]
            (반드시 "주제:", "종적/횡적 근거:", "탐구 방법:" 키워드 사용) 3가지
            [PART 4]
            (반드시 "질문:", "모범 답안:", "준비 방법:" 키워드 사용) 3가지

            @PIE [교과: %, 정시: %, 종합: %] @
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:15000]}), 지식({k_base[:10000]})
            """
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # ----------------------------------------------------
    # [수술 1] AI가 만든 이상한 괄호 문구와 제목줄을 파이썬으로 강제 삭제
    # ----------------------------------------------------
    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL).strip()

    p1_content = extract_section(clean_res, "PART 1", "PART 2")
    p2_content = extract_section(clean_res, "PART 2", "PART 3")
    p3_content = extract_section(clean_res, "PART 3", "PART 4")
    p4_content = extract_section(clean_res, "PART 4")

    # 첫 줄([PART 1] 등)에 AI가 붙인 이상한 설명글 무조건 날려버리기
    p1_body = re.sub(r'^\s*\[PART 1\].*?(?=\n|$)', '', p1_content, flags=re.IGNORECASE).strip()
    p2_body = re.sub(r'^\s*\[PART 2\].*?(?=\n|$)', '', p2_content, flags=re.IGNORECASE).strip()
    p3_body = re.sub(r'^\s*\[PART 3\].*?(?=\n|$)', '', p3_content, flags=re.IGNORECASE).strip()
    p4_body = re.sub(r'^\s*\[PART 4\].*?(?=\n|$)', '', p4_content, flags=re.IGNORECASE).strip()

    # 파트 3, 4 이모지 포맷팅
    f_p3 = re.sub(r'(?i)주제\s*:', '#### 📍 주제:', p3_body)
    f_p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '🔍 **종적/횡적 근거:**', f_p3)
    f_p3 = re.sub(r'(?i)탐구\s*방법\s*:', '🛠️ **탐구 방법:**', f_p3)

    f_p4 = re.sub(r'(?i)질문\s*:', '#### ❓ 질문:', p4_body)
    f_p4 = re.sub(r'(?i)모범\s*답안\s*:', '✅ **모범 답안:**', f_p4)
    f_p4 = re.sub(r'(?i)준비\s*방법\s*:', '🛠️ **준비 방법:**', f_p4)

    # 파이썬이 완벽하게 통제한 최종 리포트 텍스트 (다운로드/인쇄용)
    final_report_markdown = f"""
### 📝 [PART 1] 종합 진단
{p1_body}

### 🎯 [PART 2] 대입 전략
{p2_body}

### 🚀 [PART 3] 심화 탐구 가이드
{f_p3}

### 🎤 [PART 4] 면접 예상 질문
{f_p4}
"""

    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 핵심 요약"])

    # ------------------ Tab 1 ------------------
    with tab1:
        st.subheader("📊 성적 및 전형 분석")
        c1, c2, c3 = st.columns(3)
        if not st.session_state.i_df.empty: c1.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 추이"), use_container_width=True)
        if not st.session_state.m_df.empty: c2.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이", range_y=[0, 100]), use_container_width=True)
        
        pie_raw = re.search(r'@PIE\s*\[(.*?)\]\s*@', res)
        if pie_raw:
            try:
                p_items = [it.split(':') for it in pie_raw.group(1).split(',')]
                p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in p_items]
                c3.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="추천 전형"), use_container_width=True)
            except: pass
        
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1_body}")
        st.markdown(f"### 🎯 [PART 2] 대입 전략\n\n{p2_body}")

    # ------------------ Tab 2 ------------------
    with tab2:
        if p3_body:
            st.markdown("### 🚀 [PART 3] 생기부 기반 심화 탐구 로드맵")
            st.markdown(f_p3)
        if p4_body:
            st.markdown("---")
            st.markdown("### 🎤 [PART 4] 면접 예상 질문 가이드")
            st.markdown(f_p4)

    # ------------------ Tab 3 ------------------
    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 상담 질문..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}")
                st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    # ------------------ Tab 4 (완벽 인쇄 솔루션) ------------------
    with tab4:
        st.subheader("🖨️ 인쇄용 리포트")
        
        # HTML 다운로드 (에러 확률 0%)
        html_content = f"""<!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"><title>대입 컨설팅 리포트</title>
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <style>
                body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; line-height: 1.6; color: #111; max-width: 21cm; margin: auto; }}
                h1 {{ text-align: center; border-bottom: 2px solid #000; padding-bottom: 10px; margin-bottom: 5px; }}
                .dept {{ text-align: right; color: #555; font-weight: bold; margin-bottom: 30px; }}
                h3 {{ margin-top: 1.5em; color: #222; }}
                h4 {{ margin-top: 1em; color: #444; }}
                p, li {{ font-size: 10pt; }}
            </style>
        </head>
        <body onload="setTimeout(function(){{ window.print(); }}, 500);">
            <h1>대입 컨설팅 결과 리포트</h1>
            <div class="dept">지원학과: {target_major}</div>
            <div id="content"></div>
            <script>
                var rawMd = decodeURIComponent("{urllib.parse.quote(final_report_markdown)}");
                document.getElementById('content').innerHTML = marked.parse(rawMd);
            </script>
        </body>
        </html>"""

        st.download_button(
            label="📄 리포트 파일로 받아서 인쇄하기 (가장 안정적)",
            data=html_content,
            file_name=f"{target_major}_컨설팅_리포트.html",
            mime="text/html",
            use_container_width=True
        )
        
        st.info("💡 **인쇄하는 2가지 방법**\n1. 위 **[리포트 파일로 받아서 인쇄하기]** 버튼을 눌러 파일을 열면 즉시 팝업창 없이 인쇄됩니다.\n2. 키보드 **`Ctrl` + `P`** (맥은 Cmd+P)를 누르시면 지금 보시는 리포트 화면만 종이에 꽉 차게 인쇄됩니다.")
        
        st.markdown("---")
        
        # 화면 출력용 및 Ctrl+P 인쇄용 영역
        st.markdown(f"""
        <div class="print-only">
            <h1>대입 컨설팅 결과 리포트</h1>
            <p style="text-align: right; font-weight: bold;">지원학과: {target_major}</p>
            <hr>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown(f"<div class='print-only'>\n\n{final_report_markdown}\n\n</div>", unsafe_allow_html=True)
