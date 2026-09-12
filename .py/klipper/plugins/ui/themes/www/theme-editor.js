/*
 * Feather Theme Editor browser application.
 *
 * Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
 *
 * This file may be distributed under the terms of the GNU GPLv3 license
 */

"use strict";

const GENERATED_THEME_STORAGE_KEY = "feather-theme-editor.generated.v1";
const state = {
  config:null,
  doc:null,
  generatedThemes:[],
  currentGeneratedId:null,
  applyIdentity:null,
  runtime:{printer:false,can_apply:false,reason:"Checking printer…"},
  documentValid:false,
  storageWarning:"",
};
const $ = id => document.getElementById(id);

function loadGeneratedThemes() {
  try {
    const stored = JSON.parse(localStorage.getItem(GENERATED_THEME_STORAGE_KEY) || "[]");
    if (!Array.isArray(stored)) return [];

    const seen = new Set();
    return stored.filter(item => {
      const valid = item && typeof item.id === "string" && !seen.has(item.id) &&
        item.doc && typeof item.doc === "object" && item.doc.colors && typeof item.doc.colors === "object" &&
        state.config.colors.every(name => isHex(item.doc.colors[name]));
      if (valid) seen.add(item.id);
      return valid;
    });
  } catch (error) {
    console.warn("Could not load saved generated themes", error);
    state.storageWarning = "saved generated themes could not be read";
    return [];
  }
}

function saveGeneratedThemes() {
  try {
    localStorage.setItem(GENERATED_THEME_STORAGE_KEY, JSON.stringify(state.generatedThemes));
    state.storageWarning = "";
    return true;
  } catch (error) {
    console.warn("Could not save generated themes", error);
    state.storageWarning = "browser storage unavailable; generated themes last only for this session";
    return false;
  }
}

function generatedOptionValue(id) { return `generated:${id}`; }

function renderThemeOptions(selectedValue=null) {
  const select = $("themeSelect");
  select.innerHTML = "";
  for (const theme of state.config.themes) {
    const option = document.createElement("option");
    option.value = theme.file;
    option.textContent = theme.name;
    select.appendChild(option);
  }
  for (const theme of state.generatedThemes) {
    const option = document.createElement("option");
    option.value = generatedOptionValue(theme.id);
    option.textContent = `✦ ${theme.doc.name || "UNTITLED"}`;
    select.appendChild(option);
  }
  if (selectedValue && [...select.options].some(option => option.value === selectedValue)) {
    select.value = selectedValue;
  }
}

function syncDeleteThemeButton() {
  $("deleteTheme").hidden = state.currentGeneratedId === null;
}

function persistCurrentGeneratedTheme() {
  if (state.currentGeneratedId === null) return;
  const stored = state.generatedThemes.find(theme => theme.id === state.currentGeneratedId);
  if (!stored) return;

  stored.doc = exportDocument();
  stored.applyIdentity = state.applyIdentity;
  saveGeneratedThemes();
  const value = generatedOptionValue(stored.id);
  const option = [...$("themeSelect").options].find(item => item.value === value);
  if (option) option.textContent = `✦ ${stored.doc.name || "UNTITLED"}`;
}

function hex(value) { return String(value || "").trim().replace(/^#/, "").toLowerCase(); }
function isHex(value) { return /^[0-9a-f]{6}$/i.test(hex(value)); }
function css(value) { return "#" + hex(value); }
function cssVarName(prefix, name) { return `--${prefix}-${String(name).replaceAll("_", "-")}`; }

function rgbChannels(value) {
  const raw = hex(value);
  return [parseInt(raw.slice(0,2),16), parseInt(raw.slice(2,4),16), parseInt(raw.slice(4,6),16)];
}
function linearChannel(value) {
  value /= 255;
  return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
}
function luminance(value) {
  const [r,g,b] = rgbChannels(value);
  return 0.2126 * linearChannel(r) + 0.7152 * linearChannel(g) + 0.0722 * linearChannel(b);
}
function contrastRatio(foreground, background) {
  const a = luminance(foreground), b = luminance(background);
  return (Math.max(a,b) + 0.05) / (Math.min(a,b) + 0.05);
}
function diagnosticText(background) {
  return contrastRatio("000000", background) >= contrastRatio("ffffff", background) ? "000000" : "ffffff";
}

function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }
function wrapHue(value) { return (value % 360 + 360) % 360; }

function rgbToHsl(value) {
  const [rawR,rawG,rawB] = rgbChannels(value), r = rawR / 255, g = rawG / 255, b = rawB / 255;
  const high = Math.max(r,g,b), low = Math.min(r,g,b), delta = high - low;
  let hue = 0;
  if (delta) {
    if (high === r) hue = 60 * (((g - b) / delta) % 6);
    else if (high === g) hue = 60 * ((b - r) / delta + 2);
    else hue = 60 * ((r - g) / delta + 4);
  }
  const lightness = (high + low) / 2;
  const saturation = delta ? delta / (1 - Math.abs(2 * lightness - 1)) : 0;
  return {h:wrapHue(hue), s:saturation * 100, l:lightness * 100};
}

function hslToHex(hue, saturation, lightness) {
  const h = wrapHue(hue), s = clamp(saturation, 0, 100) / 100, l = clamp(lightness, 0, 100) / 100;
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const segment = h / 60, middle = chroma * (1 - Math.abs(segment % 2 - 1));
  let rgb = [0,0,0];
  if (segment < 1) rgb = [chroma,middle,0];
  else if (segment < 2) rgb = [middle,chroma,0];
  else if (segment < 3) rgb = [0,chroma,middle];
  else if (segment < 4) rgb = [0,middle,chroma];
  else if (segment < 5) rgb = [middle,0,chroma];
  else rgb = [chroma,0,middle];
  const offset = l - chroma / 2;
  return rgb.map(channel => Math.round((channel + offset) * 255).toString(16).padStart(2,"0")).join("");
}

const HARMONIES = {
  complementary: {label:"Complementary", offsets:[0,180]},
  triadic: {label:"Triadic", offsets:[0,120,240]},
  analogous: {label:"Analogous", offsets:[0,-30,30]},
  wide_analogous: {label:"Wide analogous", offsets:[0,-60,60]},
  dyadic: {label:"Dyadic −45°", offsets:[0,-45]},
  dyadic_reverse: {label:"Dyadic +45°", offsets:[0,45]},
  split: {label:"Split complementary", offsets:[0,150,210]},
  tetradic: {label:"Tetradic", offsets:[0,60,180,240]},
  square: {label:"Square", offsets:[0,90,180,270]},
};

const PALETTE_CALIBRATION = {
  subdued_neutral:[{base:"c4ccd4",t:{secondary:[0,-3.064915286503,-20.392156862745]}}],
  signal:[{base:"ff304f",t:{secondary:[0.066588327457907,0,11.56862745098]}},{base:"ffe600",t:{secondary:[-19.492096398031,0,5.4901960784314]}}],
  monochromatic:[{base:"15eb18",t:{secondary:[-6.4787053879445,15.748031496063,20.588235294118]}}],
  dual_indicator:[{base:"69d540",t:{secondary:[-26.533411146776,-4.9741388797183,-0.19607843137256]}}],
  muted_companion:[{base:"d5a444",t:{secondary:[42.775862068966,-45.619662248329,0.58823529411764]}},{base:"9ab06c",t:{secondary:[-39.019607843137,36.145270658545,-0.98039215686274]}}],
  functional_contrast:[{base:"35d9e6",t:{secondary:[81.758633872818,22.026431718062,18.43137254902]}},{base:"d9a441",t:{secondary:[130.22798332465,-19.24882629108,2.9411764705882]}}],
  neon_pair:[{base:"ff4fd8",t:{secondary:[-54.983766233766,-10.46511627907,0.78431372549019]}}],
  desktop_accent:[{base:"000080",t:{secondary:[-60,0,0]}},{base:"e95420",t:{secondary:[-60.249660786974,-40.687432867884,-25.882352941176]}}],
  cool_companion:[{base:"e0ad45",t:{secondary:[161.00775826868,-38.918283362728,-5.0980392156863]}}]
};

const DESIGNED_PALETTES = {
  subdued_neutral: {label:"Subdued / Neutral", description:"Low-chroma accents with hierarchy driven mostly by tone."},
  signal: {label:"Signal", description:"Purpose-designed neighboring signal colors; not symmetric analogous harmony."},
  monochromatic: {label:"Monochromatic", description:"One hue family separated mainly by saturation and lightness."},
  dual_indicator: {label:"Dual Indicator", description:"Two close indication colors that read as one instrument palette."},
  muted_companion: {label:"Muted Companion", description:"A vivid accent paired with a deliberately quieter companion, or vice versa."},
  functional_contrast: {label:"Functional Contrast", description:"A deliberately contrasting companion chosen for UI differentiation."},
  neon_pair: {label:"Neon Pair", description:"Two high-chroma accents intended for energetic colored interfaces."},
  desktop_accent: {label:"Desktop Accent", description:"System/desktop accents with intentionally unequal hue, chroma and lightness."},
  cool_companion: {label:"Cool Companion", description:"A warm primary paired with a quieter cool functional accent."},
};

const MATHEMATICAL_PALETTES = Object.fromEntries(Object.entries(HARMONIES).map(([id,harmony]) => [
  `harmony_${id}`,
  {label:harmony.label, description:`Mathematical ${harmony.label.toLowerCase()} hue relationship.`, harmony:id},
]));
const PALETTE_STRATEGIES = {...DESIGNED_PALETTES, ...MATHEMATICAL_PALETTES};

const REFERENCE_THEME_MATRIX = [
  {reference:"DEFAULT", base:"35d9e6", appearance:"dark", palette:"functional_contrast", distribution:"neutral_ui"},
  {reference:"DARK", base:"c4ccd4", appearance:"dark", palette:"subdued_neutral", distribution:"neutral_ui"},
  {reference:"RED", base:"ff304f", appearance:"dark", palette:"signal", distribution:"signal_terminal"},
  {reference:"YELLOW", base:"ffe600", appearance:"dark", palette:"signal", distribution:"signal_terminal"},
  {reference:"GREEN_CRT", base:"15eb18", appearance:"dark", palette:"monochromatic", distribution:"phosphor_crt"},
  {reference:"AMBER", base:"69d540", appearance:"dark", palette:"dual_indicator", distribution:"instrument_panel"},
  {reference:"INDUSTRIAL", base:"d5a444", appearance:"dark", palette:"muted_companion", distribution:"industrial_console"},
  {reference:"COMMAND", base:"9ab06c", appearance:"dark", palette:"muted_companion", distribution:"industrial_console"},
  {reference:"CYBERDECK", base:"d9a441", appearance:"dark", palette:"functional_contrast", distribution:"rugged_console"},
  {reference:"SYNTH", base:"ff4fd8", appearance:"dark", palette:"neon_pair", distribution:"neon_ui"},
  {reference:"CLASSIC", base:"000080", appearance:"light", palette:"desktop_accent", distribution:"desktop_ui"},
  {reference:"AUBERGINE", base:"e95420", appearance:"light", palette:"desktop_accent", distribution:"desktop_ui"},
  {reference:"DESERT", base:"e0ad45", appearance:"dark", palette:"cool_companion", distribution:"warm_utility_console"},
];

const THEME_CHARACTERS = {
  neutral_ui: {label:"Neutral UI", palette:"subdued_neutral", distribution:"neutral_ui", description:"Restrained, low-chroma system UI."},
  digital_contrast: {label:"Digital Contrast", palette:"functional_contrast", distribution:"neutral_ui", description:"Neutral surfaces with clearly separated functional accents."},
  signal_terminal: {label:"Signal Terminal", palette:"signal", distribution:"signal_terminal", description:"Dark signal-color terminal with strong status hierarchy."},
  phosphor_crt: {label:"Phosphor CRT", palette:"monochromatic", distribution:"phosphor_crt", description:"Monochromatic phosphor display language."},
  instrument_panel: {label:"Instrument Panel", palette:"dual_indicator", distribution:"instrument_panel", description:"Two related indicators over restrained instrument chrome."},
  industrial_console: {label:"Industrial Console", palette:"muted_companion", distribution:"industrial_console", description:"Muted material surfaces with an asymmetric accent pair."},
  rugged_console: {label:"Rugged Console", palette:"functional_contrast", distribution:"rugged_console", description:"Cold structural surfaces with warm/cool functional contrast."},
  warm_utility: {label:"Warm Utility", palette:"cool_companion", distribution:"warm_utility_console", description:"Warm material console with a quieter cool companion."},
  neon_ui: {label:"Neon UI", palette:"neon_pair", distribution:"neon_ui", description:"High-chroma accents with deliberately colored surfaces."},
  classic_desktop: {label:"Classic Desktop", palette:"desktop_accent", distribution:"desktop_ui", description:"Desktop-style chrome, selection and system accents."},
};

const DISTRIBUTION_CALIBRATION = {
  neutral_ui:{appearance:"dark",anchors:[{base:"c4ccd4",t:{background:[150,-15.686274509804,-78.039215686275],panel:[-10,-5.341446923597,-74.313725490196],primary_dark:[-2.3076923076922,-6.7207572684246,-51.56862745098],secondary_dark:[0,-2.5203491222909,-20.78431372549],warning:[-170.68965517241,39.551820728291,-21.176470588235],danger:[150,41.826678858072,-17.843137254902],danger_background:[150,30.65518890483,-71.960784313725],text:[0,-7.5781664016958,5.4901960784314],bright:[150,-15.686274509804,20],dim:[-2.7272727272727,-10.882781060022,-35.098039215686],border:[-3.3333333333333,-7.8601875532822,-57.450980392157],muted:[-4.2857142857142,-6.825515016133,-64.509803921569],success:[-77.41935483871,15.626856803327,-18.823529411765],pressed_background:[-3.3333333333333,-7.5781664016958,-58.235294117647],overlay:[150,-15.686274509804,-79.21568627451]}},{base:"35d9e6",t:{background:[10.593220338983,-37.973568281938,-53.529411764706],panel:[13.593220338983,-27.973568281938,-51.56862745098],primary_dark:[19.229583975347,-30.147481325417,-28.43137254902],secondary_dark:[33.834586466165,-39.285714285714,-40.980392156863],warning:[-139.22605676945,8.484765051395,6.8627450980392],danger:[171.21119786707,22.026431718062,9.6078431372549],danger_background:[170.13867488444,-33.973568281938,-50.588235294118],text:[11.593220338983,-53.383404347512,32.549019607843],bright:[175.59322033898,-77.973568281938,44.509803921569],dim:[14.684129429892,-66.633362096371,-17.450980392157],border:[5.4292859127535,-35.316225624596,-27.450980392157],muted:[15.593220338983,-58.824632111726,-37.058823529412],success:[-29.812185066422,-29.074889867841,0],pressed_background:[4.593220338983,-22.418012726383,-41.372549019608],overlay:[25.593220338983,-27.973568281938,-54.705882352941]}}]},
  signal_terminal:{appearance:"dark",anchors:[{base:"ff304f",t:{background:[-11.014492753623,-40,-57.450980392157],panel:[-2.2644927536232,-33.333333333333,-54.705882352941],primary_dark:[-3.6460717009916,-29.62962962963,-38.235294117647],secondary_dark:[-2.4324324324324,-36.571428571429,-36.666666666667],warning:[50.397271952259,0,-9.4117647058824],danger:[-3.0144927536232,0,-9.4117647058824],danger_background:[-0.48817696414949,0,-51.960784313725],text:[2.0624303232999,0,35.490196078431],bright:[1.4855072463768,0,39.019607843137],dim:[-1.6596540439457,-71.028037383178,-17.450980392157],border:[-2.3188405797101,-40,-36.862745098039],muted:[-4.5628798503974,-55.072463768116,-45.882352941176],success:[156.2582345191,0,3.921568627451],pressed_background:[-2.3352474706043,-37.647058823529,-42.745098039216],overlay:[-21.014492753623,-50,-58.627450980392]}},{base:"ffe600",t:{background:[-1.6176470588235,0,-48.43137254902],panel:[-1.6176470588235,-20,-46.078431372549],primary_dark:[-0.04357298474946,0,-34.117647058824],secondary_dark:[2.2633382280959,0,-29.019607843137],warning:[-12.156862745098,0,20],danger:[-50.929241261722,0,9.4117647058824],danger_background:[-41.486068111455,0,-42.549019607843],text:[-2.3529411764706,0,40],bright:[0.66496163682865,0,45.490196078431],dim:[-3.4279918864097,-73.394495412844,-7.2549019607843],border:[0,0,-30],muted:[0.028694404591107,-38.805970149254,-36.862745098039],success:[58.990461049285,0,20.980392156863],pressed_background:[-0.04357298474946,0,-34.117647058824],overlay:[5.8823529411765,0,-49.411764705882]}}]},
  phosphor_crt:{appearance:"dark",anchors:[{base:"15eb18",t:{background:[9.1588785046729,-9.251968503937,-48.627450980392],panel:[20.587449933244,-14.251968503937,-46.274509803922],primary_dark:[0.60466163720301,1.3150418053414,-31.176470588235],secondary_dark:[9.3412875963211,-48.407643312102,-40],warning:[-69.104594549219,2.2765289053894,11.960784313725],danger:[-113.74434730178,15.748031496063,13.333333333333],danger_background:[-115.03466988242,4.3194600674916,-43.333333333333],text:[-2.5233644859813,0,24.901960784314],bright:[-10.252886201209,15.748031496063,39.803921568627],dim:[2.4021217479161,-42.678934796072,-15.294117647059],border:[3.1588785046729,-12.823397075366,-25.490196078431],muted:[10.21151008362,-29.966254218223,-36.470588235294],success:[-1.7786214953271,15.748031496063,12.156862745098],pressed_background:[2.4465497375496,-4.0321882841568,-32.352941176471],overlay:[19.158878504673,15.748031496063,-49.607843137255]}}]},
  instrument_panel:{appearance:"dark",anchors:[{base:"69d540",t:{background:[6.5100671140939,-3.9484978540773,-52.352941176471],panel:[7.9386385426654,-5.6151645207439,-49.607843137255],primary_dark:[4.9311197456729,-11.654919872426,-32.941176470588],secondary_dark:[0.69053708439898,-5.006105006105,-29.411764705882],warning:[-61.416762154199,22.367291619607,8.4313725490196],danger:[-87.15857193916,13.220451917612,2.7450980392157],danger_background:[-87.489932885906,19.384835479256,-47.254901960784],text:[-1.6717510677242,-6.053761011972,23.333333333333],bright:[-3.489932885906,36.051502145923,37.450980392157],dim:[6.3405755886702,-33.058445498056,-16.862745098039],border:[8.3282489322758,-16.80564071122,-26.862745098039],muted:[18.652924256951,-27.106392590919,-39.411764705882],success:[-5.8779925873986,6.5778179353964,8.4313725490196],pressed_background:[12.442270503924,-15.981018179281,-30.196078431373],overlay:[16.510067114094,-13.948497854077,-53.529411764706]}}]},
  industrial_console:{appearance:"dark",anchors:[{base:"d5a444",t:{background:[80.275862068966,-58.556872530672,-50.980392156863],panel:[40.275862068966,-56.34203310653,-46.666666666667],primary_dark:[-1.9463601532567,-1.4867162238741,-29.411764705882],secondary_dark:[7.5,-3.0324483775811,-26.274509803922],warning:[0.12660833762223,18.388539780594,12.745098039216],danger:[-30.557471264368,0.39803686671562,0.58823529411764],danger_background:[-30.124137931034,-12.298369129311,-45.490196078431],text:[2.0149925037481,-31.811927977508,30.588235294118],bright:[0.27586206896547,-14.931680518383,38.823529411765],dim:[-3.0574712643678,-56.175920149719,-4.5098039215686],border:[10.802177858439,-51.941531783594,-22.352941176471],muted:[30.275862068965,-54.747348721148,-41.372549019608],success:[102.02586206897,-25.582928235973,3.3333333333333],pressed_background:[0.27586206896552,-24.188342509968,-41.56862745098],overlay:[80.275862068966,-52.207666181465,-53.333333333333]}},{base:"9ab06c",t:{background:[0.58823529411762,-12.441436751692,-52.352941176471],panel:[3.0882352941176,-9.0358639962739,-48.235294117647],primary_dark:[0.58823529411762,1.7296862429606,-29.803921568627],secondary_dark:[-2.2671568627451,-3.8961038961039,-24.509803921569],warning:[-39.261388766033,53.559303166917,13.137254901961],danger:[-70.789010214864,38.63578426017,-3.3333333333333],danger_background:[-65.011764705882,23.102993786481,-46.470588235294],text:[-2.8600405679513,5.7139735605812,28.43137254902],bright:[-8.1617647058824,27.054361567636,38.823529411765],dim:[-0.79107505070994,-17.954604361832,-8.8235294117647],border:[-0.38737446197993,-4.3023320532087,-24.509803921569],muted:[4.5882352941176,-9.5405503697418,-41.372549019608],success:[25.588235294118,15.911504424779,5.0980392156863],pressed_background:[0.58823529411762,-4.3742098609355,-35.098039215686],overlay:[10.588235294118,-5.0884955752212,-54.117647058824]}}]},
  rugged_console:{appearance:"dark",anchors:[{base:"d9a441",t:{background:[170.92105263158,-28.205128205128,-50.196078431373],panel:[168.92105263158,-36.054421768707,-45.686274509804],primary_dark:[-0.39473684210526,-14.611872146119,-26.666666666667],secondary_dark:[4.9238385376999,2.5821596244132,-37.843137254902],warning:[1.179117147708,17.117117117117,8.4313725490196],danger:[-35.249160134378,2.1138211382114,4.5098039215686],danger_background:[-41.687643020595,-27.683615819209,-43.725490196078],text:[94.254385964912,-57.575757575758,25.294117647059],bright:[9.3825910931174,-16.666666666667,34.509803921569],dim:[117.84412955466,-61.487383798141,-6.078431372549],border:[152.92105263158,-49.425287356322,-26.862745098039],muted:[163.42105263158,-50,-36.470588235294],success:[103.42105263158,-19.607843137255,4.7058823529412],pressed_background:[164.92105263158,-44.927536231884,-37.254901960784],overlay:[170.92105263158,-16.666666666667,-52.941176470588]}}]},
  neon_ui:{appearance:"dark",anchors:[{base:"ff4fd8",t:{background:[-52.118983957219,-26.086956521739,-60.980392156863],panel:[-48.134164222874,-31.111111111111,-56.666666666667],primary_dark:[-2.6625431530495,-38.759689922481,-40.196078431373],secondary_dark:[4.268956849602,-31.770908565651,-34.705882352941],warning:[88.665329768271,0,4.5098039215686],danger:[33.438213796254,0,-4.5098039215686],danger_background:[26.704545454545,-23.255813953488,-57.058823529412],text:[-39.382411067194,0,30],bright:[-13.295454545455,0,33.137254901961],dim:[-34.724025974026,-82.786885245902,-17.647058823529],border:[-35.487235367372,-48.951048951049,-37.450980392157],muted:[-36.97966507177,-54.761904761905,-49.019607843137],success:[-149.38484002031,-10.050251256281,-4.5098039215686],pressed_background:[-33.295454545455,-36.220472440945,-40.588235294118],overlay:[-47.581168831169,-22.222222222222,-63.725490196078]}}]},
  desktop_ui:{appearance:"light",anchors:[{base:"000080",t:{background:[120,-100,50.196078431373],panel:[160,-87.755102040816,55.686274509804],primary_dark:[120,-100,-25.098039215686],secondary_dark:[-140,-87.755102040816,55.686274509804],warning:[-180,0,0],danger:[120,0,0],danger_background:[120,-45.283018867925,64.509803921569],text:[120,-100,-25.098039215686],bright:[0,0,0],dim:[120,-100,25.098039215686],border:[120,-100,25.098039215686],muted:[120,-100,25.098039215686],success:[-120,0,0],pressed_background:[120,-100,37.647058823529],overlay:[120,-100,25.098039215686]}},{base:"e95420",t:{background:[14.477611940298,-73.707482993197,33.921568627451],panel:[14.477611940298,-66.656200941915,42.941176470588],primary_dark:[-47.830080367394,-33.290816326531,-20.588235294118],secondary_dark:[11.824046920821,19.430930266844,63.921568627451],warning:[24.395979287237,17.959183673469,-3.921568627451],danger:[-22.641032127498,-1.950318588974,-8.6274509803921],danger_background:[-27.060849598163,-22.949907235621,39.411764705882],text:[-15.522388059701,-82.040816326531,-34.705882352941],bright:[-60.249660786974,-40.687432867884,-25.882352941176],dim:[-0.52238805970154,-78.000412286127,-13.137254901961],border:[6.2957937584803,-76.622097114708,8.2352941176471],muted:[6.2957937584803,-75.963468260232,12.549019607843],success:[113.63015431318,-1.2188985183114,-23.333333333333],pressed_background:[8.4776119402984,-72.781557067271,26.862745098039],overlay:[-60.249660786974,-40.687432867884,-25.882352941176]}}]},
  warm_utility_console:{appearance:"dark",anchors:[{base:"e0ad45",t:{background:[-16.258064516129,-25.974025974026,-55.294117647059],panel:[-13.591397849462,-18.487394957983,-50.78431372549],primary_dark:[-5.7580645161291,-9.8901098901099,-31.960784313725],secondary_dark:[-0.81127733026472,2.4103468547913,-27.647058823529],warning:[2.941935483871,11.904761904762,7.2549019607843],danger:[-31.221919937816,-3.3957845433255,-5.2941176470588],danger_background:[-31.6866359447,6.3492063492064,-48.627450980392],text:[1.659743703049,-23.084200567644,12.941176470588],bright:[5.3757383007724,10.180623973727,25.490196078431],dim:[-2.0762463343109,-44.065387348969,-18.039215686275],border:[-2.5261057532425,-18.423106947697,-21.56862745098],muted:[-12.400921658986,-34.586466165414,-42.549019607843],success:[54.941935483871,-41.784302653868,-7.843137254902],pressed_background:[-7.5307917888563,-12.288786482335,-39.21568627451],overlay:[-10.258064516129,-21.428571428571,-56.666666666667]}}]}
};

const ROLE_CALIBRATION = {
  instrument_panel:[{base:"69d540",t:{button_background:[-55.489932885906,-18.493952399532,-43.529411764706],button_border:[-58.944478340452,-20.527445222498,-24.509803921569],button_text:[-61.84814184113,-5.1765680295159,-9.6078431372549],button_selected_background:[-4.1795880583198,6.7832094629959,-46.274509803922],button_selected_text:[-3.0644009710124,13.100682473792,9.8039215686274],header_background:[-54.399023794997,-19.948497854077,-44.509803921569],header_text:[-62.185585059819,-3.9484978540773,-9.2156862745098],header_border:[-59.853569249542,-20.641411239904,-29.411764705882]}}],
  industrial_console:[{base:"d5a444",t:{button_background:[40.275862068966,-57.658399934086,-44.705882352941],button_border:[9.1647509578544,-48.564678931921,-19.21568627451],button_text:[4.0258620689655,-18.035758424652,24.117647058824],button_selected_background:[29.749546279492,-39.86198716912,-39.21568627451],button_selected_text:[43.217038539554,-20.818777292576,29.21568627451],header_background:[20.275862068966,-53.794967768767,-46.862745098039],header_text:[0,0,0]}},{base:"9ab06c",t:{button_background:[2.4064171122995,-8.5198681242409,-45.686274509804],button_border:[3.566958698373,-5.9859314726571,-17.450980392157],button_text:[-5.2012383900929,6.4499659632403,23.921568627451],button_selected_background:[-36.155950752394,22.997924177865,-39.803921568627],button_selected_text:[-39.411764705882,69.911504424779,29.607843137255],header_background:[2.4064171122995,-6.6842402560723,-46.470588235294],header_text:[-39.019607843137,36.145270658545,-0.98039215686274]}}],
  rugged_console:[{base:"d9a441",t:{button_background:[167.17105263158,-39.080459770115,-43.921568627451],button_selected_background:[134.92105263158,-27.19298245614,-40.392156862745],header_background:[162.34962406015,-29.824561403509,-47.843137254902]}}],
  warm_utility_console:[{base:"e0ad45",t:{button_background:[-11.225806451613,-12.938005390836,-47.058823529412],button_border:[-2.4802867383513,-18.487394957983,-17.450980392157],button_text:[2.5990783410138,-11.672473867596,10.392156862745],button_selected_background:[-6.4552476147206,-6.2909567496723,-36.078431372549],button_selected_border:[2.4870335230866,11.274131274131,6.2745098039216],button_selected_text:[5.6794354838709,28.571428571429,30],header_background:[-16.508064516129,-0.84033613445378,-44.117647058824],header_text:[-0.94771968854286,2.1754894851341,3.921568627451],header_border:[-10.258064516129,-7.9877112135177,-20.980392156863]}}],
  desktop_ui:[{base:"000080",t:{button_selected_background:[0,0,0],button_selected_border:[120,-100,-25.098039215686],button_selected_text:[120,-100,74.901960784314],accent_background:[-60,0,0],accent_border:[120,-100,-25.098039215686],header_background:[0,0,0],header_border:[120,-100,-25.098039215686],temperature_bed:[0,0,0]}},{base:"e95420",t:{button_selected_background:[2.6594301221167,-17.334933973589,38.039215686275],button_selected_border:[0,0,0],button_selected_text:[-47.830080367394,-33.290816326531,-20.588235294118],accent_background:[-47.830080367394,-33.290816326531,-20.588235294118],accent_border:[0,0,0],header_background:[-47.830080367394,-33.290816326531,-20.588235294118],header_border:[-48.425613866153,-21.25650260104,38.039215686275],temperature_bed:[24.395979287237,17.959183673469,-3.921568627451]}}]
};

const PROFILE_COLOR_FIELDS = ["background","panel","text","bright","dim","border","muted","pressed_background","overlay"];
const STRUCTURAL_CALIBRATION_FIELDS = [
  "background","panel","primary_dark","secondary_dark","text","bright","dim","border","muted",
  "pressed_background","overlay",
];
const SEMANTIC_FIELDS = ["warning","danger","danger_background","success"];
const SEMANTIC_TARGET_HUES = {danger:355, warning:42, success:145};

function hueDistance(a, b) {
  const diff = Math.abs((((a - b) % 360) + 360) % 360);
  return Math.min(diff, 360 - diff);
}

function paletteSemanticAssignments(palette) {
  const candidates = palette.slice(2).map(item => ({...item, kind:"extra"}));
  const remaining = new Set(Object.keys(SEMANTIC_TARGET_HUES));
  const assignments = {};
  for (const candidate of candidates) {
    let bestRole = null, bestDistance = Infinity;
    for (const role of remaining) {
      const distance = hueDistance(candidate.h, SEMANTIC_TARGET_HUES[role]);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestRole = role;
      }
    }
    if (!bestRole) continue;
    assignments[bestRole] = candidate;
    remaining.delete(bestRole);
  }
  return assignments;
}

function semanticFromAccent(accentHex, templateHex, backgroundHex, makeLighter) {
  const accent = rgbToHsl(accentHex), template = rgbToHsl(templateHex);
  const saturation = clamp(Math.max(accent.s, template.s, 46), 0, 100);
  return contrastAdjustedHex(accent.h, saturation, template.l, backgroundHex, makeLighter);
}

function semanticBackgroundFromAccent(accentHex, templateHex) {
  const accent = rgbToHsl(accentHex), template = rgbToHsl(templateHex);
  const saturation = clamp(Math.max(template.s, Math.min(accent.s, 72) * 0.7), 0, 100);
  return hslToHex(accent.h, saturation, template.l);
}

function applyMathematicalSemanticPalette(colors, palette, paletteStrategy, appearance) {
  const strategy = PALETTE_STRATEGIES[paletteStrategy];
  if (!strategy?.harmony || palette.length < 3) return colors;

  const assignments = paletteSemanticAssignments(palette);
  if (assignments.warning) {
    colors.warning = semanticFromAccent(assignments.warning.hex, colors.warning, colors.panel, appearance !== "light");
  }
  if (assignments.success) {
    colors.success = semanticFromAccent(assignments.success.hex, colors.success, colors.panel, appearance !== "light");
  }
  if (assignments.danger) {
    colors.danger_background = semanticBackgroundFromAccent(assignments.danger.hex, colors.danger_background);
    colors.danger = semanticFromAccent(assignments.danger.hex, colors.danger, colors.danger_background, appearance !== "light");
  }
  return colors;
}
const PALETTE_ROLE_LABELS = ["PRIMARY","SECONDARY","ACCENT","ACCENT 2"];

function hueName(hue) {
  const h = wrapHue(hue);
  if (h < 15 || h >= 345) return "red";
  if (h < 38) return "orange";
  if (h < 52) return "amber";
  if (h < 70) return "yellow";
  if (h < 100) return "lime";
  if (h < 150) return "green";
  if (h < 180) return "teal";
  if (h < 200) return "cyan";
  if (h < 230) return "blue";
  if (h < 265) return "indigo";
  if (h < 290) return "violet";
  if (h < 325) return "magenta";
  return "rose";
}

function selectedGeneratorMode() {
  return document.querySelector('input[name="generatorMode"]:checked').value;
}

function contrastAdjustedHex(hue, saturation, lightness, background, makeLighter) {
  let value = hslToHex(hue, saturation, lightness);
  for (let step = 0; step < 30 && contrastRatio(value, background) < 4.5; step++) {
    lightness = clamp(lightness + (makeLighter ? 2 : -2), 10, 90);
    value = hslToHex(hue, saturation, lightness);
  }
  return value;
}

function hslCalibrationDistance(leftHex, rightHex) {
  const left = rgbToHsl(leftHex), right = rgbToHsl(rightHex);
  const hue = Math.abs(((left.h - right.h + 540) % 360) - 180) / 180;
  const saturation = (left.s - right.s) / 100;
  const lightness = (left.l - right.l) / 100;
  return Math.sqrt(hue * hue + saturation * saturation + lightness * lightness);
}

function interpolatedTransform(baseColor, anchors, field) {
  const base = hex(baseColor);
  const exact = anchors.find(anchor => anchor.base === base);
  if (exact) return exact.t[field];
  if (anchors.length === 1) return anchors[0].t[field];

  const weighted = anchors.map(anchor => {
    const distance = Math.max(hslCalibrationDistance(base, anchor.base), 0.000001);
    return {value:anchor.t[field], weight:1 / (distance * distance)};
  });
  const total = weighted.reduce((sum,item) => sum + item.weight, 0);
  return [0,1,2].map(index => weighted.reduce(
    (sum,item) => sum + item.value[index] * item.weight, 0) / total);
}

function applyHslTransform(value, transform) {
  const color = rgbToHsl(value);
  return hslToHex(color.h + transform[0], color.s + transform[1], color.l + transform[2]);
}

function generatedPaletteColor(value, index) {
  const raw = hex(value), color = rgbToHsl(raw);
  return {role:PALETTE_ROLE_LABELS[index] || `ACCENT ${index}`, h:color.h, hex:raw};
}

function generatePalette(baseColor, paletteStrategy) {
  const base = hex(baseColor), strategy = PALETTE_STRATEGIES[paletteStrategy];
  if (!strategy) throw new Error(`Unknown palette strategy: ${paletteStrategy}`);

  let values;
  if (strategy.harmony) {
    const baseHsl = rgbToHsl(base);
    values = HARMONIES[strategy.harmony].offsets.map((offset,index) =>
      index === 0 ? base : hslToHex(baseHsl.h + offset, baseHsl.s, baseHsl.l));
  } else {
    const anchors = PALETTE_CALIBRATION[paletteStrategy];
    values = [base, applyHslTransform(base, interpolatedTransform(base, anchors, "secondary"))];
  }
  return values.map(generatedPaletteColor);
}

function calibratedStructuralColors(palette, anchors) {
  const primary = palette[0].hex, secondary = (palette[1] || palette[0]).hex;
  const colors = {primary,secondary};
  for (const field of STRUCTURAL_CALIBRATION_FIELDS) {
    const source = field === "secondary_dark" ? secondary : primary;
    colors[field] = applyHslTransform(source, interpolatedTransform(primary, anchors, field));
  }
  return colors;
}

function calibratedSemanticColors(palette, anchors) {
  const primary = palette[0].hex, colors = {};
  for (const field of SEMANTIC_FIELDS) {
    colors[field] = applyHslTransform(primary, interpolatedTransform(primary, anchors, field));
  }
  return colors;
}

function calibratedThemeColors(palette, anchors) {
  return {...calibratedStructuralColors(palette, anchors), ...calibratedSemanticColors(palette, anchors)};
}

function profileTone(primary, rule) {
  return hslToHex(primary.h + rule[0], clamp(primary.s * rule[1], 0, rule[2]), rule[3]);
}

function profileSemantic(rule, background=null, makeLighter=false) {
  const value = hslToHex(rule[0], rule[1], rule[2]);
  return background ? contrastAdjustedHex(rule[0], rule[1], rule[2], background, makeLighter) : value;
}

function profileThemeColors(palette, profile) {
  const primaryHex = palette[0].hex, secondaryHex = (palette[1] || palette[0]).hex;
  const primary = rgbToHsl(primaryHex), secondary = rgbToHsl(secondaryHex);
  const material = profile.material_hue === undefined ? primary : {...primary,h:profile.material_hue};
  const colors = {primary:primaryHex, secondary:secondaryHex};
  for (const field of PROFILE_COLOR_FIELDS) colors[field] = profileTone(material, profile[field]);
  colors.primary_dark = applyHslTransform(primaryHex, profile.primary_dark);
  colors.secondary_dark = applyHslTransform(secondaryHex, profile.secondary_dark);
  colors.warning = profileSemantic(profile.warning, colors.panel, !profile.light);
  colors.danger_background = hslToHex(profile.danger_background[0], profile.danger_background[1], profile.danger_background[2]);
  colors.danger = profileSemantic(profile.danger, colors.danger_background, !profile.light);
  colors.success = profileSemantic(profile.success, colors.panel, !profile.light);
  return colors;
}

const OPPOSITE_APPEARANCE_PROFILES = {
  // Light recipes are deliberately different compositions, not one shared inversion.
  neutral_ui: {light:true, material_hue:210, background:[0,.03,4,96],panel:[0,.02,3,99],text:[0,.10,10,16],bright:[0,.06,6,7],dim:[0,.04,5,42],border:[0,.05,6,67],muted:[0,.04,5,78],pressed_background:[0,.07,8,86],overlay:[0,.10,10,23],primary_dark:[0,-14,-34],secondary_dark:[0,-12,-27],warning:[42,78,35],danger:[355,70,39],danger_background:[355,36,94],success:[145,52,30]},
  signal_terminal: {light:true, material_hue:48, background:[0,.12,12,95],panel:[0,.06,7,99],text:[0,.55,48,13],bright:[0,.18,18,5],dim:[0,.30,28,36],border:[0,.65,58,48],muted:[0,.25,24,70],pressed_background:[0,.55,52,82],overlay:[0,.55,55,18],primary_dark:[0,-10,-38],secondary_dark:[0,-8,-30],warning:[42,96,31],danger:[355,92,35],danger_background:[355,62,91],success:[142,70,27]},
  phosphor_crt: {light:true, background:[0,.42,36,89],panel:[0,.26,24,95],text:[0,.85,70,13],bright:[0,.70,62,6],dim:[0,.50,42,33],border:[0,.65,58,46],muted:[0,.38,34,66],pressed_background:[0,.56,50,77],overlay:[0,.70,64,13],primary_dark:[0,-8,-40],secondary_dark:[0,-20,-35],warning:[48,90,31],danger:[8,86,36],danger_background:[8,55,90],success:[122,78,25]},
  instrument_panel: {light:true, material_hue:48, background:[-18,.22,20,90],panel:[-10,.12,12,97],text:[-6,.34,32,16],bright:[-5,.25,24,7],dim:[-12,.18,18,38],border:[-14,.35,34,53],muted:[-12,.20,20,68],pressed_background:[-8,.32,32,78],overlay:[-18,.36,36,16],primary_dark:[0,-9,-36],secondary_dark:[0,-8,-32],warning:[43,84,31],danger:[12,76,36],danger_background:[12,48,90],success:[118,58,27]},
  industrial_console: {light:true, material_hue:36, background:[24,.12,11,87],panel:[16,.07,7,94],text:[10,.18,18,14],bright:[0,.12,12,5],dim:[14,.10,10,34],border:[18,.20,20,45],muted:[20,.12,12,63],pressed_background:[12,.18,18,73],overlay:[20,.18,18,13],primary_dark:[0,-12,-35],secondary_dark:[0,-10,-30],warning:[40,76,30],danger:[8,64,34],danger_background:[8,36,88],success:[145,46,27]},
  rugged_console: {light:true, material_hue:210, background:[0,.22,20,86],panel:[-5,.14,14,93],text:[-10,.30,28,13],bright:[-10,.16,16,5],dim:[-5,.18,18,34],border:[-5,.35,34,43],muted:[-5,.20,20,62],pressed_background:[-5,.30,30,71],overlay:[0,.35,34,12],primary_dark:[0,-12,-35],secondary_dark:[0,-12,-33],warning:[42,80,30],danger:[4,72,35],danger_background:[4,42,88],success:[148,54,27]},
  neon_ui: {light:true, background:[0,.55,55,89],panel:[0,.35,36,96],text:[0,.72,66,12],bright:[0,.32,34,4],dim:[0,.38,38,31],border:[0,.78,76,55],muted:[0,.42,42,70],pressed_background:[0,.65,65,76],overlay:[0,.82,82,12],primary_dark:[0,-25,-43],secondary_dark:[0,-20,-38],warning:[42,100,30],danger:[348,100,36],danger_background:[345,72,89],success:[164,82,25]},
  desktop_ui: {light:false, background:[0,.10,12,5],panel:[0,.12,14,12],text:[0,.08,8,87],bright:[0,.10,10,96],dim:[0,.06,7,52],border:[0,.08,9,34],muted:[0,.07,8,20],pressed_background:[0,.10,11,25],overlay:[0,.35,32,3],primary_dark:[0,-28,-30],secondary_dark:[0,-22,-28],warning:[45,90,64],danger:[355,76,63],danger_background:[355,45,11],success:[145,55,58]},
  warm_utility_console: {light:true, material_hue:32, background:[-10,.34,30,88],panel:[-7,.18,18,95],text:[-12,.30,30,15],bright:[-10,.18,18,6],dim:[-10,.20,20,36],border:[-8,.36,36,49],muted:[-8,.22,22,65],pressed_background:[-6,.32,32,75],overlay:[-10,.40,40,14],primary_dark:[0,-10,-37],secondary_dark:[0,-10,-32],warning:[43,82,30],danger:[8,72,35],danger_background:[8,46,89],success:[102,52,27]},
};

function baseGeneratedRoles() {
  return {temperature_nozzle:"secondary",temperature_bed:"warning",temperature_fan:"primary"};
}

function neutralUiRoles(palette, appearance) {
  if (appearance === "dark") return baseGeneratedRoles();
  return {
    ...baseGeneratedRoles(), button_background:"panel", button_border:"border", button_text:"text",
    button_selected_background:"primary_dark", button_selected_border:"primary", button_selected_text:"ffffff",
    accent_background:"muted", accent_border:"primary", accent_text:"primary_dark",
    header_background:"panel", header_text:"primary_dark", header_border:"border",
  };
}

function signalTerminalRoles(palette, appearance) {
  if (appearance === "dark") return baseGeneratedRoles();
  return {
    temperature_nozzle:"danger", temperature_bed:"warning", temperature_fan:"secondary",
    button_background:"background", button_border:"primary", button_text:"primary_dark",
    button_selected_background:"primary_dark", button_selected_border:"secondary", button_selected_text:"ffffff",
    accent_background:"secondary_dark", accent_border:"secondary", accent_text:"ffffff",
    header_background:"primary_dark", header_text:"ffffff", header_border:"primary",
  };
}

function phosphorCrtRoles(palette, appearance) {
  if (appearance === "dark") return baseGeneratedRoles();
  return {
    temperature_nozzle:"primary", temperature_bed:"warning", temperature_fan:"secondary",
    button_background:"panel", button_border:"primary_dark", button_text:"primary_dark",
    button_selected_background:"primary_dark", button_selected_border:"primary", button_selected_text:"ffffff",
    accent_background:"pressed_background", accent_border:"secondary", accent_text:"primary_dark",
    header_background:"primary_dark", header_text:"ffffff", header_border:"primary",
  };
}

function neonUiRoles(palette, appearance) {
  if (appearance === "dark") return baseGeneratedRoles();
  return {
    temperature_nozzle:"secondary", temperature_bed:"warning", temperature_fan:"primary",
    button_background:"panel", button_border:"secondary", button_text:"text",
    button_selected_background:"primary_dark", button_selected_border:"primary", button_selected_text:"ffffff",
    accent_background:"secondary_dark", accent_border:"secondary", accent_text:"ffffff",
    header_background:"secondary_dark", header_text:"ffffff", header_border:"primary",
  };
}

function calibratedRoleValues(palette, anchors, fields) {
  const primary = palette[0].hex, out = {};
  for (const field of fields) out[field] = applyHslTransform(primary, interpolatedTransform(primary, anchors, field));
  return out;
}

function instrumentPanelRoles(palette, appearance) {
  if (appearance === "light") return {
    ...baseGeneratedRoles(), button_background:"panel", button_border:"border", button_text:"text",
    button_selected_background:"primary_dark", button_selected_border:"primary", button_selected_text:"ffffff",
    accent_background:"primary_dark", accent_border:"primary", accent_text:"ffffff",
    header_background:"secondary_dark", header_text:"ffffff", header_border:"secondary",
  };
  return {
    ...baseGeneratedRoles(),
    ...calibratedRoleValues(palette, ROLE_CALIBRATION.instrument_panel, [
      "button_background","button_border","button_text","button_selected_background","button_selected_text",
      "header_background","header_text","header_border",
    ]),
    button_selected_border:"primary",
  };
}

function industrialConsoleRoles(palette, appearance) {
  if (appearance === "light") return {
    ...baseGeneratedRoles(), button_background:"panel", button_border:"border", button_text:"text",
    button_selected_background:"secondary_dark", button_selected_border:"secondary", button_selected_text:"ffffff",
    accent_background:"primary_dark", accent_border:"primary", accent_text:"ffffff",
    header_background:"muted", header_text:"text", header_border:"border",
  };
  return {
    ...baseGeneratedRoles(),
    ...calibratedRoleValues(palette, ROLE_CALIBRATION.industrial_console, [
      "button_background","button_border","button_text","button_selected_background","button_selected_text",
      "header_background","header_text",
    ]),
    button_selected_border:"secondary", header_border:"border",
  };
}

function ruggedConsoleRoles(palette, appearance) {
  if (appearance === "light") return {
    temperature_nozzle:"danger",temperature_bed:"warning",temperature_fan:"secondary",
    button_background:"panel",button_border:"primary_dark",button_text:"text",
    button_selected_background:"secondary_dark",button_selected_border:"secondary",button_selected_text:"ffffff",
    accent_background:"primary_dark",accent_border:"primary",accent_text:"ffffff",
    header_background:"muted",header_text:"text",header_border:"border",
  };
  return {
    temperature_nozzle:"danger",temperature_bed:"warning",temperature_fan:"secondary",
    ...calibratedRoleValues(palette, ROLE_CALIBRATION.rugged_console, [
      "button_background","button_selected_background","header_background",
    ]),
    button_border:"primary",button_text:"bright",button_selected_border:"secondary",button_selected_text:"bright",
    header_text:"primary",header_border:"border",
  };
}

function warmUtilityRoles(palette, appearance) {
  if (appearance === "light") return {
    ...baseGeneratedRoles(), button_background:"panel",button_border:"border",button_text:"text",
    button_selected_background:"primary_dark",button_selected_border:"primary",button_selected_text:"ffffff",
    accent_background:"primary_dark",accent_border:"primary",accent_text:"ffffff",
    header_background:"muted",header_text:"text",header_border:"border",
  };
  return {
    ...baseGeneratedRoles(),
    ...calibratedRoleValues(palette, ROLE_CALIBRATION.warm_utility_console, [
      "button_background","button_border","button_text","button_selected_background","button_selected_border",
      "button_selected_text","header_background","header_text","header_border",
    ]),
  };
}

function desktopRoles(palette, appearance) {
  if (appearance === "dark") return {
    temperature_nozzle:"primary",temperature_bed:"warning",temperature_fan:"secondary",
    button_background:"panel",button_border:"border",button_text:"text",
    button_selected_background:"primary_dark",button_selected_border:"primary",button_selected_text:"bright",
    accent_background:"secondary_dark",accent_border:"secondary",accent_text:"bright",
    header_background:"primary_dark",header_text:"bright",header_border:"secondary_dark",
  };
  return {
    button_background:"panel",button_border:"border",button_text:"text",
    ...calibratedRoleValues(palette, ROLE_CALIBRATION.desktop_ui, [
      "button_selected_background","button_selected_border","button_selected_text","accent_background",
      "accent_border","header_background","header_border","temperature_bed",
    ]),
    accent_text:"ffffff",header_text:"ffffff",temperature_nozzle:"primary",temperature_fan:"secondary",
  };
}

const DISTRIBUTIONS = {
  neutral_ui: {label:"Neutral UI",description:"Restrained neutral surfaces; accent character comes from the selected palette.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.neutral_ui.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.neutral_ui),roles:neutralUiRoles},
  signal_terminal: {label:"Signal Terminal",description:"Signal-first hierarchy over terminal-like surfaces.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.signal_terminal.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.signal_terminal),roles:signalTerminalRoles},
  phosphor_crt: {label:"Phosphor CRT",description:"Monochrome display surfaces and phosphor-oriented status colors.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.phosphor_crt.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.phosphor_crt),roles:phosphorCrtRoles},
  instrument_panel: {label:"Instrument Panel",description:"Instrument chrome with two related indication colors.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.instrument_panel.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.instrument_panel),roles:instrumentPanelRoles},
  industrial_console: {label:"Industrial Console",description:"Material neutral surfaces with deliberate muted/vivid accent hierarchy.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.industrial_console.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.industrial_console),roles:industrialConsoleRoles},
  rugged_console: {label:"Rugged Console",description:"Cold structural surfaces with functionally separated accent roles.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.rugged_console.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.rugged_console),roles:ruggedConsoleRoles},
  neon_ui: {label:"Neon UI",description:"High-chroma accents and intentionally colored surfaces.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.neon_ui.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.neon_ui),roles:neonUiRoles},
  desktop_ui: {label:"Desktop UI",description:"Classic desktop chrome, selection and system-role composition.",dark:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.desktop_ui),light:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.desktop_ui.anchors),roles:desktopRoles},
  warm_utility_console: {label:"Warm Utility Console",description:"Warm material surfaces with restrained utility controls.",dark:palette => calibratedThemeColors(palette,DISTRIBUTION_CALIBRATION.warm_utility_console.anchors),light:palette => profileThemeColors(palette,OPPOSITE_APPEARANCE_PROFILES.warm_utility_console),roles:warmUtilityRoles},
};

function generateThemeColors(palette, distribution, appearance) {
  const strategy = DISTRIBUTIONS[distribution];
  if (!strategy) throw new Error(`Unknown distribution: ${distribution}`);
  const generator = strategy[appearance];
  if (!generator) throw new Error(`Distribution ${distribution} does not support ${appearance} appearance`);
  return generator(palette);
}

function completeGeneratedRoles(candidates, defaults) {
  return Object.fromEntries(Object.keys(defaults).map(role => [role,candidates[role] ?? defaults[role]]));
}

function generateThemeRoles(colors, palette, distribution, appearance, defaults) {
  const strategy = DISTRIBUTIONS[distribution];
  return completeGeneratedRoles(strategy.roles(palette,appearance,colors), defaults);
}

function generateTheme(baseColor, paletteStrategy, distribution, appearance, overrides=[], defaults={}) {
  const generated = generatePalette(baseColor,paletteStrategy);
  const palette = generated.map((color,index) => {
    const override = overrides[index];
    return override ? generatedPaletteColor(override,index) : color;
  });
  const colors = applyMathematicalSemanticPalette(generateThemeColors(palette,distribution,appearance), palette, paletteStrategy, appearance);
  const roles = generateThemeRoles(colors,palette,distribution,appearance,defaults);
  return {palette,colors,roles};
}

function generatorSelection() {
  const base = hex($("generatorColor").value), paletteStrategy = $("generatorPaletteStrategy").value;
  const appearance = selectedGeneratorMode(), distribution = $("generatorDistribution").value;
  const palette = currentGeneratorPalette(base,paletteStrategy).map((color,index) => {
    const override = generatorPaletteOverrides[index];
    return override ? generatedPaletteColor(override,index) : color;
  });
  const colors = applyMathematicalSemanticPalette(generateThemeColors(palette,distribution,appearance), palette, paletteStrategy, appearance);
  for (const name of ["danger","warning","success"]) {
    if (generatorSemanticOverrides[name]) colors[name] = generatorSemanticOverrides[name];
  }
  const roles = generateThemeRoles(colors,palette,distribution,appearance,state.config.defaults);
  const names = palette.map(color => hueName(color.h));
  const character = matchingThemeCharacter(paletteStrategy,distribution);
  return {base,paletteStrategy,appearance,distribution,character,names,palette,colors,roles};
}

function generatedThemeName(selection) {
  return `${selection.appearance}_${selection.names[0]}`.toUpperCase().slice(0,32);
}

function generatedDescription(selection) {
  const palette = PALETTE_STRATEGIES[selection.paletteStrategy].label;
  const distribution = DISTRIBUTIONS[selection.distribution].label;
  const mode = selection.appearance[0].toUpperCase() + selection.appearance.slice(1);
  return `${mode} ${distribution}; ${palette} palette.`;
}

function generateThemePalette(selection) {
  return {colors:selection.colors, roles:selection.roles};
}

function resolveRoleValue(value) {
  const raw = String(value || "").trim().toLowerCase().replace(/^#/, "");
  return state.doc.colors[raw] || raw;
}
function effectiveRoles() {
  const out = {};
  for (const role of state.config.roles) {
    const source = (state.doc.roles || {})[role] ?? state.config.defaults[role];
    out[role] = resolveRoleValue(source);
  }
  return out;
}
function roleSource(role) { return (state.doc.roles || {})[role] ?? state.config.defaults[role]; }
function exportDocument() {
  const copy = JSON.parse(JSON.stringify(state.doc));
  copy.name = $("themeName").value.trim();
  copy.description = $("themeDescription").value.trim();
  return copy;
}

function setCssVariables() {
  const root = document.documentElement;
  for (const name of state.config.colors) root.style.setProperty(cssVarName("c", name), css(state.doc.colors[name]));
  const roles = effectiveRoles();
  for (const name of state.config.roles) root.style.setProperty(cssVarName("r", name), css(roles[name]));
  root.style.setProperty("--diag-background", css(diagnosticText(state.doc.colors.background)));
  root.style.setProperty("--diag-panel", css(diagnosticText(state.doc.colors.panel)));
}

function swatchHtml(name, value, extra="") {
  const raw = hex(value), label = diagnosticText(raw), ratio = contrastRatio(label, raw);
  return `<div class="swatch" style="background:${css(raw)};color:${css(label)}">
    <b>${name}</b><span>#${raw.toUpperCase()}</span>
    <span class="meta">${extra || `label ${ratio.toFixed(2)}:1`}</span>
  </div>`;
}

function renderPhysicalMap() {
  const clusters = new Map();
  const add = (value, kind, name) => {
    const raw = hex(value);
    if (!clusters.has(raw)) clusters.set(raw, {colors:[],roles:[]});
    clusters.get(raw)[kind].push(name);
  };
  for (const name of state.config.colors) add(state.doc.colors[name], "colors", name);
  const roles = effectiveRoles();
  for (const role of state.config.roles) add(roles[role], "roles", role);

  const items = [...clusters.entries()].sort((a,b) => {
    const ac = a[1].colors.length + a[1].roles.length;
    const bc = b[1].colors.length + b[1].roles.length;
    return bc - ac || a[0].localeCompare(b[0]);
  });

  $("physicalMap").innerHTML = items.map(([value, info]) => {
    const label = diagnosticText(value);
    return `<div class="physical-item">
      <div class="physical-color" style="background:${css(value)};color:${css(label)}">#${value.toUpperCase()}</div>
      <div class="physical-meta"><b>#${value.toUpperCase()}</b><br>
        ThemeColor: ${info.colors.join(", ") || "—"}<br>
        ThemeRole: ${info.roles.join(", ") || "—"}
      </div>
    </div>`;
  }).join("");
}

function renderRuntimeCombinations() {
  const c = state.doc.colors, r = effectiveRoles();
  const cases = [
    ["INFO / NETWORK", "BRIGHT ON BACKGROUND", c.bright, c.background, c.border, "BRIGHT → BACKGROUND"],
    ["TOAST / ACCENT", "SECONDARY ON BACKGROUND", c.secondary, c.background, c.secondary, "SECONDARY → BACKGROUND"],
    ["CALIBRATION CURRENT", "CURRENT STAGE", c.bright, c.secondary_dark, c.secondary, "BRIGHT / SECONDARY_DARK / SECONDARY border"],
    ["CALIBRATION DONE", "DONE STAGE", c.primary, c.panel, c.primary, "PRIMARY → PANEL"],
    ["FUTURE / DISABLED", "FUTURE STAGE", c.muted, c.panel, c.border, "MUTED → PANEL"],
    ["TOGGLE OFF / AUX", "DIM / MUTED", c.dim, c.panel, c.muted, "DIM text + MUTED structure → PANEL"],
    ["DANGER ACTION", "ABORT / CANCEL", c.danger, c.danger_background, c.danger, "DANGER → DANGER_BACKGROUND"],
    ["SELECTED ROLE SET", "SELECTED", r.button_selected_text, r.button_selected_background, r.button_selected_border, "resolved selected button roles"],
    ["ACCENT ROLE SET", "ACCENT", r.accent_text, r.accent_background, r.accent_border, "resolved accent surface roles"],
  ];

  $("runtimeGrid").innerHTML = cases.map(([title,text,fg,bg,border,detail]) => {
    const ratio = contrastRatio(fg,bg);
    const ratioColor = ratio >= 4.5 ? c.success : ratio >= 3.0 ? c.warning : c.danger;
    return `<div class="runtime-item"><div class="runtime-head"><b>${title}</b>
      <span class="runtime-ratio" style="color:${css(ratioColor)}">${ratio.toFixed(2)}:1</span></div>
      <div class="runtime-sample" style="color:${css(fg)};background:${css(bg)};border-color:${css(border)}">${text}</div>
      <div class="runtime-detail">${detail}</div></div>`;
  }).join("");
}

const ROLE_SPECIMEN_GROUPS = [
  {key:"button", title:"BUTTON", roles:[
    ["BG", "button_background"], ["BORDER", "button_border"], ["TEXT", "button_text"],
  ]},
  {key:"selected", title:"SELECTED BUTTON", roles:[
    ["BG", "button_selected_background"], ["BORDER", "button_selected_border"],
    ["TEXT", "button_selected_text"],
  ]},
  {key:"accent", title:"ACCENT SURFACE", roles:[
    ["BG", "accent_background"], ["BORDER", "accent_border"], ["TEXT", "accent_text"],
  ]},
  {key:"header", title:"HEADER", roles:[
    ["BG", "header_background"], ["BORDER", "header_border"], ["TEXT", "header_text"],
  ]},
  {key:"temperature", title:"TEMPERATURES", roles:[
    ["NOZZLE", "temperature_nozzle"], ["BED", "temperature_bed"], ["FAN", "temperature_fan"],
  ]},
];

function roleCombinationSample(group) {
  if (group.key === "button") return `<button class="role-mini-button"
    style="background:var(--r-button-background);color:var(--r-button-text);
      border:2px solid var(--r-button-border)">BUTTON</button>`;
  if (group.key === "selected") return `<button class="role-mini-button"
    style="background:var(--r-button-selected-background);color:var(--r-button-selected-text);
      border:2px solid var(--r-button-selected-border)">SELECTED</button>`;
  if (group.key === "accent") return `<div class="role-mini-header"
    style="background:var(--r-accent-background);color:var(--r-accent-text);
      border:2px solid var(--r-accent-border)">ACCENT</div>`;
  if (group.key === "header") return `<div class="role-mini-header"
    style="background:var(--r-header-background);color:var(--r-header-text);
      border:2px solid var(--r-header-border)">HEADER</div>`;
  return `<div class="role-mini-temperatures">
    <span style="color:var(--r-temperature-nozzle)">NOZZLE</span>
    <span style="color:var(--r-temperature-bed)">BED</span>
    <span style="color:var(--r-temperature-fan)">FAN</span>
  </div>`;
}

function renderRoleSpecimens() {
  const groupedRoles = ROLE_SPECIMEN_GROUPS.flatMap(group => group.roles.map(item => item[1]));
  const missing = state.config.roles.filter(role => !groupedRoles.includes(role));
  const unknown = groupedRoles.filter(role => !state.config.roles.includes(role));
  if (missing.length || unknown.length || new Set(groupedRoles).size !== groupedRoles.length) {
    throw new Error("ThemeRole specimen groups do not match the theme contract");
  }

  $("roleSpecimens").innerHTML = ROLE_SPECIMEN_GROUPS.map(group => `
    <div class="role-combination" data-combination="${group.key}">
      <div class="role-combination-head"><h3>${group.title}</h3><span>${group.roles.length} COLORS</span></div>
      <div class="role-combination-sample">${roleCombinationSample(group)}</div>
      <div class="role-combination-colors">${group.roles.map(([label, role]) => `
        <div class="role-color-item" data-role="${role}">
          <div class="role-color-head"><b>${label}</b><span class="role-source-badge"></span></div>
          <div class="role-color-value">
            <input class="role-chip-color role-current-picker" type="color" title="Edit current role color">
            <code class="current-label"></code>
          </div>
          <div class="role-color-meta"><code>${role}</code><span class="role-source-label"></span></div>
        </div>`).join("")}</div>
    </div>`).join("");

  for (const item of $("roleSpecimens").querySelectorAll(".role-color-item")) {
    const role = item.dataset.role;
    const currentPicker = item.querySelector(".role-current-picker");

    const applyPicker = picker => {
      state.doc.roles ||= {};
      const value = hex(picker.value);
      state.doc.roles[role] = value;
      syncRoleEditorToCustom(role, value);
      applyLiveState(picker);
    };

    currentPicker.addEventListener("input", () => applyPicker(currentPicker));
    currentPicker.addEventListener("change", () => applyLiveState());
  }
  syncRoleSpecimens();
}

function syncRoleSpecimens(activePicker=null) {
  const roles = effectiveRoles();
  for (const item of $("roleSpecimens").querySelectorAll(".role-color-item")) {
    const role = item.dataset.role;
    const current = roles[role];
    const source = roleSource(role);
    const inherited = (state.doc.roles || {})[role] === undefined;
    const currentPicker = item.querySelector(".role-current-picker");
    const badge = item.querySelector(".role-source-badge");

    badge.textContent = inherited ? "DEFAULT" : "OVERRIDE";
    badge.classList.toggle("override", !inherited);
    item.querySelector(".current-label").textContent = `#${hex(current).toUpperCase()}`;
    item.querySelector(".role-source-label").textContent = `${inherited ? "default" : "source"}: ${source}`;
    if (currentPicker !== activePicker) currentPicker.value = css(current);
  }
}

function renderColorEditor() {
  const root = $("colorGrid"); root.innerHTML = "";
  for (const name of state.config.colors) {
    const value = hex(state.doc.colors[name]);
    const row = document.createElement("div"); row.className = "color-row"; row.dataset.color = name;
    row.innerHTML = `<code>${name}</code><input class="picker" type="color" value="#${value}"><input class="hex-input" type="text" value="${value}" maxlength="7">`;
    const picker = row.querySelector(".picker"), input = row.querySelector(".hex-input");
    picker.addEventListener("input", () => {
      const next = hex(picker.value); state.doc.colors[name] = next; input.value = next; applyLiveState(picker);
    });
    picker.addEventListener("change", () => applyLiveState());
    input.addEventListener("input", () => {
      const next = hex(input.value); if (!isHex(next)) return;
      state.doc.colors[name] = next; picker.value = css(next); applyLiveState(input);
    });
    root.appendChild(row);
  }
}

function roleOptions(selected) {
  const raw = String(selected || "").trim().toLowerCase();
  const isToken = state.config.colors.includes(raw);
  return state.config.colors.map(name => `<option value="${name}" ${raw === name ? "selected" : ""}>${name}</option>`).join("") +
    `<option value="__custom__" ${!isToken ? "selected" : ""}>custom HEX</option>`;
}
function renderRoleEditor() {
  const root = $("roleGrid"); root.innerHTML = "";
  for (const role of state.config.roles) {
    const source = roleSource(role), raw = String(source).trim().toLowerCase(), custom = !state.config.colors.includes(raw);
    const row = document.createElement("div"); row.className = "role-row" + (custom ? " custom" : ""); row.dataset.role = role;
    row.innerHTML = `<code>${role}</code><select>${roleOptions(raw)}</select><input class="role-color" type="color" value="#${isHex(raw) ? hex(raw) : "ffffff"}">`;
    const select = row.querySelector("select"), picker = row.querySelector(".role-color");
    select.addEventListener("change", () => {
      state.doc.roles ||= {};
      if (select.value === "__custom__") {
        row.classList.add("custom"); const value = effectiveRoles()[role] || "ffffff";
        picker.value = css(value); state.doc.roles[role] = hex(value);
      } else {
        row.classList.remove("custom"); state.doc.roles[role] = select.value;
      }
      applyLiveState();
    });
    picker.addEventListener("input", () => {
      state.doc.roles ||= {}; state.doc.roles[role] = hex(picker.value); applyLiveState(picker);
    });
    picker.addEventListener("change", () => applyLiveState());
    root.appendChild(row);
  }
}

function syncRoleEditorToCustom(role, value) {
  const row = [...document.querySelectorAll(".role-row")].find(item => item.dataset.role === role);
  if (!row) return;
  row.classList.add("custom");
  row.querySelector("select").value = "__custom__";
  row.querySelector(".role-color").value = css(value);
}

function syncColorEditors(active=null) {
  for (const row of document.querySelectorAll(".color-row")) {
    const name = row.dataset.color, value = hex(state.doc.colors[name]);
    const picker = row.querySelector(".picker"), input = row.querySelector(".hex-input");
    if (picker !== active) picker.value = css(value);
    if (input !== active) input.value = value;
  }
}

function updateJsonBox() { $("jsonBox").value = JSON.stringify(exportDocument(), null, 2) + "\n"; }
let validationTimer = null;
function scheduleValidation() { clearTimeout(validationTimer); validationTimer = setTimeout(validateCurrent, 120); }

function syncSaveActions() {
  $("downloadButton").disabled = !state.documentValid;
  $("applyButton").disabled = !state.documentValid || !state.runtime.can_apply;
  $("applyHint").textContent = state.runtime.can_apply ?
    "Saves the theme on this printer and activates it." : state.runtime.reason;
}

async function refreshRuntime() {
  try {
    const response = await fetch("/api/runtime");
    state.runtime = await response.json();
  } catch (error) {
    state.runtime = {printer:false,can_apply:false,reason:`Printer check failed: ${error.message}`};
  }
  syncSaveActions();
  return state.runtime;
}

function applyLiveState(activeControl=null) {
  // One authoritative update path. No role specimen DOM is rebuilt here, so
  // an open native color picker remains open while every dependent view updates.
  effectiveRoles();
  setCssVariables();
  syncColorEditors(activeControl);
  syncRoleSpecimens(activeControl);
  renderPhysicalMap();
  renderRuntimeCombinations();
  updateJsonBox();
  persistCurrentGeneratedTheme();
  scheduleValidation();
}

async function validateCurrent() {
  const response = await fetch("/api/validate", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(exportDocument())});
  const result = await response.json(), status = $("validationStatus");
  state.documentValid = result.ok;
  if (result.ok) {
    status.className = "status ok";
    status.textContent = "Valid • " + (result.schema_used ? "schema validation OK" : "basic validation OK");
  } else {
    status.className = "status bad"; status.textContent = result.errors.join(" • ");
  }
  if (state.storageWarning) status.textContent += " • " + state.storageWarning;
  syncSaveActions();
}

let generatorNameEdited = false;
let generatorPaletteOverrides = [];
let generatorSemanticOverrides = {};
let generatorPaletteCache = {key:null,palette:[]};

function currentGeneratorPalette(base, strategy) {
  const key = `${hex(base)}:${strategy}`;
  if (generatorPaletteCache.key !== key) {
    generatorPaletteCache = {key,palette:generatePalette(base,strategy)};
  }
  return generatorPaletteCache.palette;
}

function appendSelectOption(parent, value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  parent.appendChild(option);
}

function populateGeneratorControls() {
  const character = $("generatorCharacter"), palette = $("generatorPaletteStrategy");
  const distribution = $("generatorDistribution");
  character.innerHTML = "";
  for (const [id,item] of Object.entries(THEME_CHARACTERS)) appendSelectOption(character,id,item.label);
  appendSelectOption(character,"custom","Custom");

  palette.innerHTML = "";
  const designed = document.createElement("optgroup");
  designed.label = "Designed palettes";
  for (const [id,item] of Object.entries(DESIGNED_PALETTES)) appendSelectOption(designed,id,item.label);
  palette.appendChild(designed);
  const mathematical = document.createElement("optgroup");
  mathematical.label = "Mathematical harmonies";
  for (const [id,item] of Object.entries(MATHEMATICAL_PALETTES)) appendSelectOption(mathematical,id,item.label);
  palette.appendChild(mathematical);

  distribution.innerHTML = "";
  for (const [id,item] of Object.entries(DISTRIBUTIONS)) appendSelectOption(distribution,id,item.label);
}

function matchingThemeCharacter(palette, distribution) {
  const match = Object.entries(THEME_CHARACTERS).find(([,character]) =>
    character.palette === palette && character.distribution === distribution);
  return match ? match[0] : "custom";
}

function syncGeneratorCharacter() {
  $("generatorCharacter").value = matchingThemeCharacter(
    $("generatorPaletteStrategy").value, $("generatorDistribution").value);
}

function applyGeneratorAccent(picker) {
  const index = Number(picker.dataset.index);
  generatorPaletteOverrides[index] = hex(picker.value);
  const selection = generatorSelection(), color = selection.palette[index];
  const swatch = picker.closest(".generator-swatch"), label = diagnosticText(color.hex);
  swatch.style.background = css(color.hex);
  swatch.style.color = css(label);
  swatch.querySelector("b").textContent = color.role;
  swatch.querySelector(".hue-name").textContent = hueName(color.h);
  swatch.querySelector(".hex-label").textContent = `#${color.hex.toUpperCase()}`;
  picker.setAttribute("aria-label", `Adjust ${color.role.toLowerCase()} accent`);
  renderGeneratorSemanticPalette(selection);
  renderGeneratorMiniPreview(selection);
  if (!generatorNameEdited) $("generatorName").value = generatedThemeName(selection);
}

function resolvedGeneratedRole(roles, colors, role) {
  const source = roles[role] ?? state.config.defaults[role];
  return colors[source] || source;
}

function applyGeneratorSemantic(picker) {
  const name = picker.dataset.semantic;
  generatorSemanticOverrides[name] = hex(picker.value);
  const selection = generatorSelection(), value = selection.colors[name];
  const swatch = picker.closest(".generator-semantic-swatch"), label = diagnosticText(value);
  swatch.style.background = css(value);
  swatch.style.color = css(label);
  swatch.querySelector(".hue-name").textContent = hueName(rgbToHsl(value).h);
  swatch.querySelector(".hex-label").textContent = `#${value.toUpperCase()}`;
  renderGeneratorMiniPreview(selection);
}

function renderGeneratorSemanticPalette(selection) {
  const root = $("generatorSemanticPalette");
  const semantic = [
    ["danger","DANGER",selection.colors.danger],
    ["warning","WARNING",selection.colors.warning],
    ["success","SUCCESS",selection.colors.success],
  ];
  root.innerHTML = semantic.map(([key,name,value]) => {
    const label = diagnosticText(value);
    return `<label class="generator-semantic-swatch" style="background:${css(value)};color:${css(label)}">
      <input type="color" value="${css(value)}" data-semantic="${key}" aria-label="Adjust ${name.toLowerCase()} color">
      <b>${name}</b><span class="generator-swatch-meta"><span class="hue-name">${hueName(rgbToHsl(value).h)}</span>
      <span class="hex-label">#${value.toUpperCase()}</span></span></label>`;
  }).join("");
  for (const picker of root.querySelectorAll('input[type="color"]')) {
    picker.addEventListener("input", () => applyGeneratorSemantic(picker));
  }
}

function renderGeneratorMiniPreview(selection) {
  const {colors, roles} = generateThemePalette(selection);
  const root = $("generatorMiniPreview");
  const variables = {
    background:colors.background, panel:colors.panel, text:colors.text,
    bright:colors.bright, dim:colors.dim, border:colors.border,
    primary:colors.primary, secondary:colors.secondary,
    danger:colors.danger, danger_background:colors.danger_background,
    header_background:resolvedGeneratedRole(roles, colors, "header_background"),
    header_text:resolvedGeneratedRole(roles, colors, "header_text"),
    header_border:resolvedGeneratedRole(roles, colors, "header_border"),
    button_background:resolvedGeneratedRole(roles, colors, "button_background"),
    button_text:resolvedGeneratedRole(roles, colors, "button_text"),
    button_border:resolvedGeneratedRole(roles, colors, "button_border"),
    selected_background:resolvedGeneratedRole(roles, colors, "button_selected_background"),
    selected_text:resolvedGeneratedRole(roles, colors, "button_selected_text"),
    selected_border:resolvedGeneratedRole(roles, colors, "button_selected_border"),
    accent_background:resolvedGeneratedRole(roles, colors, "accent_background"),
    accent_text:resolvedGeneratedRole(roles, colors, "accent_text"),
    accent_border:resolvedGeneratedRole(roles, colors, "accent_border"),
  };
  for (const [name,value] of Object.entries(variables)) {
    root.style.setProperty(`--gp-${name.replaceAll("_", "-")}`, css(value));
  }
}

function refreshGeneratorPreview(resetPalette=false, resetSemantic=false) {
  if (resetPalette) {
    generatorPaletteOverrides = [];
    generatorPaletteCache = {key:null,palette:[]};
  }
  if (resetSemantic) generatorSemanticOverrides = {};
  syncGeneratorCharacter();
  const selection = generatorSelection(), root = $("generatorPalette");
  root.style.gridTemplateColumns = `repeat(${selection.palette.length},1fr)`;
  root.innerHTML = selection.palette.map((color,index) => {
    const label = diagnosticText(color.hex);
    return `<label class="generator-swatch" style="background:${css(color.hex)};color:${css(label)}">
      <input type="color" value="${css(color.hex)}" data-index="${index}"
        aria-label="Adjust ${color.role.toLowerCase()} accent">
      <b>${color.role}</b><span class="generator-swatch-meta"><span class="hue-name">${hueName(color.h)}</span>
      <span class="hex-label">#${color.hex.toUpperCase()}</span></span></label>`;
  }).join("");
  for (const picker of root.querySelectorAll('input[type="color"]')) {
    picker.addEventListener("input", () => applyGeneratorAccent(picker));
  }
  renderGeneratorSemanticPalette(selection);
  renderGeneratorMiniPreview(selection);
  if (!generatorNameEdited) $("generatorName").value = generatedThemeName(selection);
}

function openGenerator() {
  if (!state.doc) return;
  const primary = state.doc.colors.primary;
  if (isHex(primary)) {
    $("generatorColor").value = css(primary);
    $("generatorHex").value = hex(primary);
  }
  const appearance = luminance(state.doc.colors.background) > .45 ? "light" : "dark";
  document.querySelector(`input[name="generatorMode"][value="${appearance}"]`).checked = true;

  const reference = REFERENCE_THEME_MATRIX.find(item => item.base === hex(primary) && item.appearance === appearance);
  const fallbackCharacter = appearance === "light" ? THEME_CHARACTERS.classic_desktop :
    (rgbToHsl(primary).s < 30 ? THEME_CHARACTERS.neutral_ui : THEME_CHARACTERS.digital_contrast);
  const initial = reference || fallbackCharacter;
  $("generatorPaletteStrategy").value = initial.palette;
  $("generatorDistribution").value = initial.distribution;
  syncGeneratorCharacter();
  generatorNameEdited = false;
  generatorSemanticOverrides = {};
  refreshGeneratorPreview(true);
  $("generatorDialog").showModal();
}

function normalizeThemeName(value, fallback) {
  let name = String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]+/g,"_").replace(/^_+|_+$/g,"");
  if (!name) name = fallback;
  if (!/^[A-Z]/.test(name)) name = "THEME_" + name;
  return name.slice(0,32).replace(/_+$/g,"");
}

function customThemeName(value) {
  const suffix = "_CUSTOM";
  const base = normalizeThemeName(value, "THEME").slice(0, 32 - suffix.length).replace(/_+$/g, "");
  return `${base || "THEME"}${suffix}`;
}

function newGeneratedThemeId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2,8)}`;
}

function showCurrentDocument(name, description) {
  state.doc.roles ||= {};
  $("themeName").value = name;
  $("themeDescription").value = description;
  syncDeleteThemeButton();
  renderColorEditor();
  renderRoleEditor();
  renderRoleSpecimens();
  applyLiveState();
}

function createGeneratedTheme() {
  const selection = generatorSelection(), fallbackName = generatedThemeName(selection);
  const next = JSON.parse(JSON.stringify(exportDocument()));
  next.name = normalizeThemeName($("generatorName").value, fallbackName);
  next.description = generatedDescription(selection);
  delete next.copyright;
  const {colors, roles} = generateThemePalette(selection);
  next.colors = {...next.colors, ...colors};
  next.roles = roles;

  state.doc = next;
  state.currentGeneratedId = newGeneratedThemeId();
  state.applyIdentity = `generated:${state.currentGeneratedId}`;
  state.generatedThemes.push({
    id:state.currentGeneratedId,
    applyIdentity:state.applyIdentity,
    doc:JSON.parse(JSON.stringify(next)),
  });
  saveGeneratedThemes();
  renderThemeOptions(generatedOptionValue(state.currentGeneratedId));
  showCurrentDocument(next.name, next.description);
  $("generatorDialog").close();
}

async function loadTheme(filename) {
  state.currentGeneratedId = null;
  state.applyIdentity = `bundled:${filename}`;
  const response = await fetch("/api/theme?file=" + encodeURIComponent(filename));
  state.doc = await response.json();
  const baseName = state.doc.name || filename.replace(/\.json$/i, "");
  const name = customThemeName(baseName);
  showCurrentDocument(name, state.doc.description || "");
}

function loadGeneratedTheme(id) {
  const stored = state.generatedThemes.find(theme => theme.id === id);
  if (!stored) return;

  state.currentGeneratedId = id;
  state.applyIdentity = stored.applyIdentity || `generated:${id}`;
  stored.applyIdentity = state.applyIdentity;
  state.doc = JSON.parse(JSON.stringify(stored.doc));
  showCurrentDocument(state.doc.name || "", state.doc.description || "");
}

async function deleteCurrentGeneratedTheme() {
  const id = state.currentGeneratedId;
  const stored = state.generatedThemes.find(theme => theme.id === id);
  if (!stored || !confirm(`Delete saved theme "${stored.doc.name || "UNTITLED"}"?`)) return;
  const deletedIndex = $("themeSelect").selectedIndex;

  state.generatedThemes = state.generatedThemes.filter(theme => theme.id !== id);
  state.currentGeneratedId = null;
  state.applyIdentity = null;
  saveGeneratedThemes();
  renderThemeOptions();
  syncDeleteThemeButton();

  const select = $("themeSelect");
  if (select.options.length) {
    select.selectedIndex = Math.min(Math.max(deletedIndex, 0), select.options.length - 1);
    if (select.value.startsWith("generated:")) {
      loadGeneratedTheme(select.value.slice("generated:".length));
    } else {
      await loadTheme(select.value);
    }
  } else {
    $("validationStatus").className = "status bad";
    $("validationStatus").textContent = "No themes available.";
    state.documentValid = false;
    syncSaveActions();
  }
}

async function downloadCurrentTheme() {
  $("saveMenu").open = false;
  const response = await fetch("/api/export", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(exportDocument())});
  if (!response.ok) { const result = await response.json(); alert(result.errors.join("\n")); return; }
  const blob = await response.blob(), disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/), filename = match ? match[1] : "custom-theme.json";
  const url = URL.createObjectURL(blob), link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function applyCurrentTheme() {
  $("saveMenu").open = false;
  const runtime = await refreshRuntime(), status = $("validationStatus");
  if (!runtime.can_apply) {
    status.className = "status bad";
    status.textContent = runtime.reason;
    return;
  }

  status.className = "status";
  status.textContent = "Saving and applying theme…";
  const response = await fetch("/api/apply", {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({identity:state.applyIdentity,document:exportDocument()}),
  });
  const result = await response.json();
  if (!response.ok) {
    status.className = "status bad";
    status.textContent = (result.errors || ["Theme could not be applied."]).join(" • ");
    await refreshRuntime();
    return;
  }

  status.className = "status ok";
  status.textContent = `Applied ${result.name} • ${result.filename} • splash/${result.screen_filename}`;
  persistCurrentGeneratedTheme();
}

function bindEditorEvents() {
  const select = $("themeSelect");
  select.addEventListener("change", () => {
    if (select.value.startsWith("generated:")) loadGeneratedTheme(select.value.slice("generated:".length));
    else loadTheme(select.value);
  });
  $("themeName").addEventListener("input", () => {
    updateJsonBox(); persistCurrentGeneratedTheme(); scheduleValidation();
  });
  $("themeDescription").addEventListener("input", () => {
    updateJsonBox(); persistCurrentGeneratedTheme(); scheduleValidation();
  });
  $("deleteTheme").addEventListener("click", deleteCurrentGeneratedTheme);
  $("generateButton").addEventListener("click", openGenerator);
  $("saveMenu").addEventListener("toggle", () => {
    if ($("saveMenu").open) refreshRuntime();
  });
  $("downloadButton").addEventListener("click", downloadCurrentTheme);
  $("applyButton").addEventListener("click", applyCurrentTheme);
  $("applyJson").addEventListener("click", () => {
    try {
      state.doc = JSON.parse($("jsonBox").value); state.doc.roles ||= {};
      $("themeName").value = state.doc.name || ""; $("themeDescription").value = state.doc.description || "";
      renderColorEditor(); renderRoleEditor(); renderRoleSpecimens(); applyLiveState();
    } catch (error) { alert(error.message); }
  });
  $("copyJson").addEventListener("click", async () => navigator.clipboard.writeText($("jsonBox").value));
}

function bindGeneratorEvents() {
  populateGeneratorControls();
  $("generatorClose").addEventListener("click", () => $("generatorDialog").close());
  $("generatorCancel").addEventListener("click", () => $("generatorDialog").close());
  $("generatorCreate").addEventListener("click", createGeneratedTheme);
  $("generatorColor").addEventListener("input", () => {
    $("generatorHex").value = hex($("generatorColor").value);
    refreshGeneratorPreview(true,true);
  });
  $("generatorHex").addEventListener("input", () => {
    const value = hex($("generatorHex").value);
    if (!isHex(value)) return;
    $("generatorColor").value = css(value);
    refreshGeneratorPreview(true,true);
  });
  $("generatorHex").addEventListener("blur", () => {
    $("generatorHex").value = hex($("generatorColor").value);
  });
  $("generatorCharacter").addEventListener("change", () => {
    const id = $("generatorCharacter").value;
    if (id === "custom") return;
    const character = THEME_CHARACTERS[id];
    const paletteChanged = $("generatorPaletteStrategy").value !== character.palette;
    $("generatorPaletteStrategy").value = character.palette;
    $("generatorDistribution").value = character.distribution;
    refreshGeneratorPreview(paletteChanged,true);
  });
  $("generatorPaletteStrategy").addEventListener("change", () => refreshGeneratorPreview(true,true));
  $("generatorDistribution").addEventListener("change", () => refreshGeneratorPreview(false,true));
  for (const radio of document.querySelectorAll('input[name="generatorMode"]')) {
    radio.addEventListener("change", () => refreshGeneratorPreview(false,true));
  }
  $("generatorName").addEventListener("input", () => { generatorNameEdited = true; });
}

async function loadInitialTheme() {
  const select = $("themeSelect");
  if (state.config.themes.length) {
    select.value = state.config.themes[0].file;
    await loadTheme(select.value);
  } else if (state.generatedThemes.length) {
    select.value = generatedOptionValue(state.generatedThemes[0].id);
    loadGeneratedTheme(state.generatedThemes[0].id);
  } else {
    $("validationStatus").className = "status bad";
    $("validationStatus").textContent = "No theme JSON files found.";
    state.documentValid = false;
    syncSaveActions();
  }
}

async function init() {
  state.config = await (await fetch("/api/config")).json();
  await refreshRuntime();
  state.generatedThemes = loadGeneratedThemes();
  renderThemeOptions();
  bindEditorEvents();
  bindGeneratorEvents();
  await loadInitialTheme();
}
init();
