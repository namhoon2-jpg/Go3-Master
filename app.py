import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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
    model = genai.GenerativeModel("gemini-2.5-flash") 
except Exception as e:
    st.error(f"⚠️ 시스템 설정 오류: {e}")

if "analysis_result" not in st.session_state: st.session_state.analysis_result = ""
if "chat_history" not in st.session_state: st.session_state.chat_history = []

# ==========================================
# 2. 화면 및 인쇄 스타일 (CSS)
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
        .print-only h2 { font-size: 18pt; text-align: center; border-bottom: 2px solid black; padding-bottom: 10px; margin-bottom: 15px;}
        .print-only h3 { font-size: 14pt; margin-top: 20px; color: #111; }
        .print-only h4 { font-size: 12pt; margin-top: 10px; color: #333; }
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
    if not match: return ""
    content = match.group(0).strip()
    content = re.sub(rf"^.*?\[{start_keyword}\].*?(?=\n|$)", "", content, flags=re.IGNORECASE).strip()
    return content

# ==========================================
# 4. 메인 UI 구성
# ==========================================
st.set_page_config(page_title="고3 대입 전문 컨설팅 시스템", layout="wide")
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
# 5. 분석 로직 (프롬프트 강화 - 레이더 차트 수치 포함)
# ==========================================
if excel_file and pdf_file and target_major:
    if not st.session_state.analysis_result:
        with st.spinner('AI 엔진이 데이터를 정밀 분석 중입니다...'):
            i_df, m_df = process_performance_data(excel_file.getvalue())
            with pdfplumber.open(pdf_file) as p: pdf_text = "".join([pg.extract_text() for pg in p.pages])
            k_base = sync_knowledge()
            
            rural_inst = "이 학생은 [농어촌 전형] 대상자임." if is_rural else ""
            
            prompt = f"""
            대한민국 일반고 입시 전문 컨설턴트로서 {target_major} 지망 학생을 분석하세요. {rural_inst}
            데이터: 내신({i_df.to_string()}), 모의고사({m_df.to_string()}), 생기부({pdf_text[:12000]}), 지식({k_base[:5000]})
            
            [절대 준수 규칙]
            1. 인사말, 서론, 결론 절대 금지. 즉시 [PART 1]부터 출력할 것.
            2. 모든 문장은 글머리 기호('-')를 사용한 개괄식 작성.
            3. 철저한 '음슴체(~함, ~임, ~됨)' 사용.
            4. 정시 합격 확률이 낮으면 @PIE 내 정시 비중을 10% 이하로 조정.
            5. [PART 1]의 진단 내용과 마지막 @PIE의 추천 전형 비중이 완벽하게 논리적으로 일치해야 함.

            [작성 가이드]
            [PART 1] 종합 진단: 
                     - 내신 등급 추이, 모의고사 강/약점, 전공 적합성을 수치와 데이터를 기반으로 4줄 이상 매우 상세하고 풍성하게 분석할 것.
                     - 특히, 지원 전공({target_major})과 관련하여 반드시 있어야 할 핵심 과목(예: 수학, 과학 등) 중 세특 기록이 누락되었거나 내용이 부실한 부분을 날카롭게 지적할 것.
            [PART 2] 대입 전략 및 추천 도서: 
                     - 전략: 남은 학기 동안의 성적 관리 및 생기부 보완을 위한 실질적이고 구체적인 액션 플랜을 4줄 이상 제시.
                     - 추천 도서: 지원 학과와 관련된 심화 추천 도서 3권과 그 이유.
            [PART 3] 심화 탐구 가이드 (3가지 작성)
                     - 반드시 "주제:", "종적/횡적 근거:", "탐구 방법:" 키워드를 사용할 것.
                     - "종적/횡적 근거:" 작성 시 허구의 내용을 지어내지 말고, 반드시 제공된 생기부 데이터에서 정확한 출처(예: '1학년 1학기 자율활동의 ~내용')를 명시할 것.
            [PART 4] 면접 예상 질문: 농어촌 질문 금지. 전공 심화 질문 3가지. "모범 답안"과 "준비 방법"을 실전에서 바로 쓸 수 있도록 압도적으로 디테일하게 작성할 것. (반드시 "질문:", "모범 답안:", "준비 방법:" 키워드 사용)

            마지막 줄에 반드시 아래 두 가지 태그를 차례대로 포함할 것.
            @PIE [교과: 60, 정시: 10, 종합: 30] @
            @RADAR [전공적합성: 85, 학업역량: 70, 진로탐색: 90, 리더십/인성: 80, 발전가능성: 75] @ (각 100점 만점으로 생기부를 냉정하게 평가할 것)
            """
            
            response = model.generate_content(prompt)
            st.session_state.analysis_result = response.text
            st.session_state.i_df, st.session_state.m_df = i_df, m_df

    # ----------------------------------------------------
    # 데이터 후처리 및 출력
    # ----------------------------------------------------
    res = st.session_state.analysis_result
    clean_res = re.sub(r'@.*?@', '', res, flags=re.DOTALL).strip()

    p1_body = extract_section(clean_res, "PART 1", "PART 2")
    p2_body = extract_section(clean_res, "PART 2", "PART 3")
    p3_body = extract_section(clean_res, "PART 3", "PART 4")
    p4_body = extract_section(clean_res, "PART 4")

    f_p3 = re.sub(r'(?i)주제\s*:', '#### 📍 주제:', p3_body)
    f_p3 = re.sub(r'(?i)종적/횡적\s*근거\s*:', '🔍 **종적/횡적 근거:**', f_p3)
    f_p3 = re.sub(r'(?i)탐구\s*방법\s*:', '🛠️ **탐구 방법:**', f_p3)

    f_p4 = re.sub(r'(?i)질문\s*:', '#### ❓ 질문:', p4_body)
    f_p4 = re.sub(r'(?i)모범\s*답안\s*:', '✅ **모범 답안:**', f_p4)
    f_p4 = re.sub(r'(?i)준비\s*방법\s*:', '🛠️ **준비 방법:**', f_p4)

    final_report_markdown = f"""
### 📝 [PART 1] 종합 진단
{p1_body}

### 🎯 [PART 2] 대입 전략 및 추천 도서
{p2_body}

### 🚀 [PART 3] 심화 탐구 가이드
{f_p3}

### 🎤 [PART 4] 면접 예상 질문
{f_p4}
"""

    tab1, tab2, tab3, tab4 = st.tabs(["📝 진단 및 전략", "🚀 심화 탐구 가이드", "💬 실시간 상담", "🖨️ 핵심 요약 인쇄"])

    with tab1:
        st.subheader("📊 성적 및 생기부 역량 분석")
        
        # 4분할 레이아웃 적용 (내신, 모의고사, 추천전형, 생기부 레이더)
        col1, col2 = st.columns(2)
        col3, col4 = st.columns(2)
        
        with col1:
            if not st.session_state.i_df.empty: 
                st.plotly_chart(px.line(st.session_state.i_df, x="학기", y="등급", markers=True, range_y=[9, 1], title="내신 추이"), use_container_width=True)
        
        with col2:
            if not st.session_state.m_df.empty: 
                st.plotly_chart(px.line(st.session_state.m_df, x="시험", y=["국어", "수학", "영어", "탐구"], markers=True, title="모의고사 추이", range_y=[0, 100]), use_container_width=True)
        
        with col3:
            pie_raw = re.search(r'@PIE\s*\[(.*?)\]\s*@', res)
            if pie_raw:
                try:
                    p_items = [it.split(':') for it in pie_raw.group(1).split(',')]
                    p_data = [{"전형": k.strip(), "비중": int(re.sub(r'[^0-9]', '', v))} for k, v in p_items]
                    st.plotly_chart(px.pie(pd.DataFrame(p_data), values="비중", names="전형", hole=0.4, title="추천 전형"), use_container_width=True)
                except: pass

        with col4:
            radar_raw = re.search(r'@RADAR\s*\[(.*?)\]\s*@', res)
            if radar_raw:
                try:
                    r_items = [it.split(':') for it in radar_raw.group(1).split(',')]
                    r_labels = [k.strip() for k, v in r_items]
                    r_values = [int(re.sub(r'[^0-9]', '', v)) for k, v in r_items]
                    
                    fig = go.Figure()
                    fig.add_trace(go.Scatterpolar(
                        r=r_values + [r_values[0]], # 닫힌 다각형을 위해 첫 값 추가
                        theta=r_labels + [r_labels[0]],
                        fill='toself',
                        name='생기부 역량'
                    ))
                    fig.update_layout(
                        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
                        showlegend=False,
                        title="생기부 종합 역량 진단"
                    )
                    st.plotly_chart(fig, use_container_width=True)
                except Exception as e:
                    st.error(f"레이더 차트 생성 오류: {e}")
        
        st.divider()
        st.markdown(f"### 📝 [PART 1] 종합 진단\n\n{p1_body}")
        st.markdown(f"### 🎯 [PART 2] 대입 전략 및 추천 도서\n\n{p2_body}")

    with tab2:
        st.markdown(f"### 🚀 [PART 3] 심화 탐구 가이드\n\n{f_p3}")
        st.divider()
        st.markdown(f"### 🎤 [PART 4] 면접 예상 질문\n\n{f_p4}")

    with tab3:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])
        if p_chat := st.chat_input("추가 상담 질문..."):
            st.session_state.chat_history.append({"role": "user", "content": p_chat})
            with st.chat_message("user"): st.markdown(p_chat)
            with st.chat_message("assistant"):
                ans = model.generate_content(f"배경: {res}\n질문: {p_chat}")
                st.markdown(ans.text); st.session_state.chat_history.append({"role": "assistant", "content": ans.text})

    with tab4:
        st.subheader("🖨️ 인쇄용 리포트")
        
        html_content = f"""<!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8"><title>대입 컨설팅 리포트</title>
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <style>
                body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; line-height: 1.6; color: #111; max-width: 21cm; margin: auto; }}
                h2 {{ text-align: center; border-bottom: 2px solid #000; padding-bottom: 10px; margin-bottom: 30px; }}
                h3 {{ margin-top: 1.5em; color: #222; font-size: 14pt; }}
                h4 {{ margin-top: 1em; color: #444; font-size: 12pt; }}
                p, li {{ font-size: 10pt; }}
            </style>
        </head>
        <body onload="setTimeout(function(){{ window.print(); }}, 500);">
            <h2>대입 컨설팅 결과 리포트 ({target_major})</h2>
            <div id="content"></div>
            <script>
                var rawMd = decodeURIComponent("{urllib.parse.quote(final_report_markdown)}");
                document.getElementById('content').innerHTML = marked.parse(rawMd);
            </script>
        </body>
        </html>"""

        st.download_button(
            label="📄 리포트 파일로 받아서 인쇄하기",
            data=html_content,
            file_name=f"{target_major}_컨설팅_리포트.html",
            mime="text/html",
            use_container_width=True
        )
        
        st.markdown("---")
        st.markdown(f"""
        <div class="print-only">
            <h2>대입 컨설팅 결과 리포트 ({target_major})</h2>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"<div class='print-only'>\n\n{final_report_markdown}\n\n</div>", unsafe_allow_html=True)
