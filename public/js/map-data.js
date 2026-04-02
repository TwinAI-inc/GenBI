/**
 * GenBI Map Data & Rendering
 * Simplified US state + World country boundary polygons for canvas rendering.
 * All coordinates pre-projected to 0–1000 range.
 * Exports: window.GenBIMaps = { usStates, worldCountries, drawUSMapChart, drawWorldMapChart }
 */
(function () {
  "use strict";

  /* ========================================================================
   *  1.  US STATES  – Albers USA projection, 0-1000 coords
   *      Each entry: { id, name, poly: [[{x,y},...], ...] }
   *      poly is an array of rings (most states have one ring).
   * ======================================================================== */
  const usStates = [
    {id:"AL",name:"Alabama",poly:[[{x:680,y:540},{x:700,y:540},{x:710,y:555},{x:715,y:575},{x:718,y:600},{x:720,y:630},{x:715,y:650},{x:705,y:660},{x:695,y:658},{x:680,y:655},{x:672,y:640},{x:668,y:620},{x:665,y:595},{x:668,y:570},{x:672,y:550}]]},
    {id:"AK",name:"Alaska",poly:[[{x:80,y:780},{x:120,y:760},{x:160,y:750},{x:200,y:755},{x:230,y:770},{x:250,y:790},{x:240,y:810},{x:220,y:830},{x:190,y:840},{x:155,y:835},{x:125,y:825},{x:100,y:810},{x:85,y:795}]]},
    {id:"AZ",name:"Arizona",poly:[[{x:220,y:530},{x:260,y:525},{x:290,y:528},{x:300,y:540},{x:305,y:560},{x:300,y:590},{x:295,y:620},{x:280,y:640},{x:255,y:645},{x:230,y:635},{x:215,y:615},{x:210,y:590},{x:212,y:560}]]},
    {id:"AR",name:"Arkansas",poly:[[{x:590,y:540},{x:620,y:535},{x:645,y:538},{x:655,y:550},{x:658,y:570},{x:655,y:595},{x:645,y:610},{x:625,y:615},{x:600,y:610},{x:585,y:595},{x:582,y:570},{x:585,y:550}]]},
    {id:"CA",name:"California",poly:[[{x:100,y:350},{x:120,y:340},{x:140,y:345},{x:155,y:360},{x:165,y:385},{x:170,y:415},{x:172,y:450},{x:168,y:485},{x:160,y:520},{x:148,y:545},{x:130,y:555},{x:110,y:545},{x:95,y:520},{x:85,y:490},{x:80,y:455},{x:82,y:420},{x:88,y:385}]]},
    {id:"CO",name:"Colorado",poly:[[{x:310,y:380},{x:395,y:380},{x:395,y:450},{x:310,y:450}]]},
    {id:"CT",name:"Connecticut",poly:[[{x:840,y:310},{x:860,y:305},{x:870,y:310},{x:872,y:325},{x:865,y:335},{x:850,y:338},{x:838,y:330},{x:835,y:318}]]},
    {id:"DE",name:"Delaware",poly:[[{x:820,y:380},{x:832,y:375},{x:838,y:385},{x:836,y:400},{x:828,y:408},{x:818,y:402},{x:815,y:390}]]},
    {id:"FL",name:"Florida",poly:[[{x:700,y:650},{x:730,y:640},{x:760,y:645},{x:780,y:660},{x:790,y:685},{x:785,y:715},{x:770,y:740},{x:750,y:760},{x:730,y:770},{x:715,y:755},{x:705,y:730},{x:698,y:700},{x:695,y:670}]]},
    {id:"GA",name:"Georgia",poly:[[{x:710,y:530},{x:735,y:525},{x:755,y:535},{x:765,y:555},{x:768,y:580},{x:765,y:610},{x:755,y:635},{x:735,y:645},{x:715,y:640},{x:700,y:625},{x:695,y:600},{x:698,y:570},{x:705,y:545}]]},
    {id:"HI",name:"Hawaii",poly:[[{x:300,y:810},{x:320,y:800},{x:340,y:805},{x:350,y:820},{x:345,y:835},{x:330,y:842},{x:310,y:838},{x:298,y:825}]]},
    {id:"ID",name:"Idaho",poly:[[{x:195,y:220},{x:215,y:210},{x:235,y:215},{x:248,y:230},{x:255,y:260},{x:252,y:295},{x:245,y:330},{x:230,y:350},{x:210,y:355},{x:195,y:340},{x:188,y:310},{x:185,y:275},{x:188,y:245}]]},
    {id:"IL",name:"Illinois",poly:[[{x:600,y:340},{x:620,y:335},{x:635,y:345},{x:642,y:365},{x:645,y:395},{x:642,y:430},{x:635,y:460},{x:625,y:485},{x:610,y:495},{x:595,y:485},{x:585,y:460},{x:582,y:430},{x:585,y:395},{x:590,y:360}]]},
    {id:"IN",name:"Indiana",poly:[[{x:640,y:350},{x:660,y:345},{x:672,y:355},{x:678,y:375},{x:680,y:405},{x:678,y:435},{x:672,y:460},{x:658,y:470},{x:642,y:462},{x:635,y:440},{x:632,y:410},{x:632,y:380},{x:635,y:360}]]},
    {id:"IA",name:"Iowa",poly:[[{x:520,y:310},{x:560,y:305},{x:595,y:310},{x:610,y:325},{x:612,y:345},{x:605,y:365},{x:590,y:375},{x:560,y:378},{x:530,y:372},{x:515,y:355},{x:512,y:335}]]},
    {id:"KS",name:"Kansas",poly:[[{x:420,y:420},{x:520,y:420},{x:520,y:485},{x:420,y:485}]]},
    {id:"KY",name:"Kentucky",poly:[[{x:640,y:455},{x:670,y:448},{x:700,y:450},{x:730,y:455},{x:745,y:465},{x:750,y:478},{x:740,y:492},{x:720,y:500},{x:690,y:505},{x:660,y:502},{x:640,y:495},{x:630,y:480},{x:632,y:465}]]},
    {id:"LA",name:"Louisiana",poly:[[{x:580,y:620},{x:610,y:615},{x:635,y:620},{x:650,y:635},{x:658,y:660},{x:650,y:685},{x:635,y:695},{x:610,y:698},{x:585,y:690},{x:572,y:670},{x:570,y:645}]]},
    {id:"ME",name:"Maine",poly:[[{x:870,y:195},{x:890,y:185},{x:905,y:190},{x:915,y:210},{x:918,y:235},{x:912,y:260},{x:900,y:275},{x:882,y:278},{x:868,y:268},{x:860,y:248},{x:858,y:225},{x:862,y:205}]]},
    {id:"MD",name:"Maryland",poly:[[{x:780,y:380},{x:800,y:375},{x:818,y:378},{x:830,y:385},{x:835,y:398},{x:828,y:410},{x:812,y:415},{x:792,y:412},{x:778,y:402},{x:775,y:390}]]},
    {id:"MA",name:"Massachusetts",poly:[[{x:850,y:290},{x:875,y:282},{x:895,y:285},{x:905,y:295},{x:900,y:308},{x:885,y:315},{x:865,y:315},{x:848,y:308},{x:845,y:298}]]},
    {id:"MI",name:"Michigan",poly:[[{x:620,y:240},{x:645,y:230},{x:665,y:235},{x:680,y:250},{x:688,y:275},{x:685,y:305},{x:675,y:330},{x:658,y:345},{x:635,y:348},{x:618,y:335},{x:608,y:310},{x:605,y:280},{x:610,y:255}]]},
    {id:"MN",name:"Minnesota",poly:[[{x:490,y:195},{x:520,y:188},{x:548,y:192},{x:565,y:210},{x:572,y:235},{x:570,y:268},{x:562,y:298},{x:545,y:315},{x:520,y:318},{x:498,y:308},{x:485,y:288},{x:480,y:260},{x:482,y:230}]]},
    {id:"MS",name:"Mississippi",poly:[[{x:630,y:555},{x:650,y:550},{x:665,y:558},{x:670,y:575},{x:672,y:600},{x:670,y:630},{x:662,y:655},{x:648,y:665},{x:632,y:660},{x:620,y:640},{x:615,y:615},{x:618,y:585},{x:622,y:565}]]},
    {id:"MO",name:"Missouri",poly:[[{x:540,y:400},{x:575,y:395},{x:605,y:400},{x:620,y:415},{x:628,y:440},{x:625,y:470},{x:615,y:495},{x:595,y:510},{x:565,y:512},{x:540,y:505},{x:525,y:485},{x:520,y:458},{x:522,y:430}]]},
    {id:"MT",name:"Montana",poly:[[{x:245,y:185},{x:290,y:178},{x:340,y:180},{x:380,y:185},{x:395,y:200},{x:392,y:225},{x:380,y:250},{x:355,y:258},{x:310,y:255},{x:268,y:248},{x:248,y:232},{x:242,y:210}]]},
    {id:"NE",name:"Nebraska",poly:[[{x:380,y:340},{x:430,y:335},{x:490,y:338},{x:520,y:345},{x:525,y:365},{x:518,y:388},{x:500,y:400},{x:460,y:405},{x:415,y:402},{x:388,y:390},{x:378,y:370},{x:375,y:352}]]},
    {id:"NV",name:"Nevada",poly:[[{x:155,y:340},{x:175,y:332},{x:198,y:338},{x:210,y:360},{x:218,y:390},{x:220,y:425},{x:215,y:465},{x:205,y:495},{x:188,y:510},{x:168,y:505},{x:152,y:485},{x:145,y:455},{x:142,y:420},{x:145,y:380}]]},
    {id:"NH",name:"New Hampshire",poly:[[{x:862,y:235},{x:875,y:228},{x:885,y:235},{x:888,y:252},{x:885,y:272},{x:878,y:285},{x:865,y:288},{x:855,y:278},{x:852,y:258},{x:855,y:242}]]},
    {id:"NJ",name:"New Jersey",poly:[[{x:825,y:340},{x:838,y:335},{x:848,y:342},{x:852,y:358},{x:850,y:378},{x:842,y:395},{x:830,y:400},{x:820,y:392},{x:815,y:372},{x:818,y:352}]]},
    {id:"NM",name:"New Mexico",poly:[[{x:270,y:500},{x:310,y:495},{x:350,y:498},{x:365,y:515},{x:368,y:545},{x:362,y:580},{x:350,y:610},{x:330,y:625},{x:300,y:628},{x:275,y:618},{x:260,y:595},{x:255,y:565},{x:258,y:532}]]},
    {id:"NY",name:"New York",poly:[[{x:780,y:260},{x:810,y:250},{x:840,y:252},{x:862,y:260},{x:872,y:278},{x:868,y:300},{x:855,y:318},{x:835,y:328},{x:810,y:332},{x:788,y:325},{x:775,y:308},{x:772,y:285}]]},
    {id:"NC",name:"North Carolina",poly:[[{x:710,y:470},{x:740,y:465},{x:775,y:468},{x:810,y:475},{x:835,y:485},{x:845,y:498},{x:838,y:512},{x:818,y:520},{x:788,y:522},{x:755,y:518},{x:725,y:512},{x:708,y:498},{x:705,y:482}]]},
    {id:"ND",name:"North Dakota",poly:[[{x:400,y:190},{x:440,y:185},{x:485,y:188},{x:505,y:200},{x:508,y:220},{x:502,y:242},{x:488,y:255},{x:460,y:258},{x:425,y:255},{x:405,y:242},{x:398,y:222},{x:396,y:205}]]},
    {id:"OH",name:"Ohio",poly:[[{x:680,y:340},{x:705,y:335},{x:725,y:342},{x:738,y:358},{x:742,y:380},{x:740,y:408},{x:732,y:432},{x:718,y:448},{x:698,y:452},{x:680,y:442},{x:670,y:422},{x:668,y:395},{x:670,y:365}]]},
    {id:"OK",name:"Oklahoma",poly:[[{x:400,y:480},{x:440,y:475},{x:480,y:478},{x:520,y:482},{x:545,y:490},{x:555,y:505},{x:548,y:522},{x:530,y:535},{x:500,y:540},{x:460,y:538},{x:425,y:532},{x:400,y:520},{x:392,y:502}]]},
    {id:"OR",name:"Oregon",poly:[[{x:100,y:225},{x:135,y:215},{x:170,y:218},{x:198,y:225},{x:210,y:245},{x:208,y:270},{x:198,y:298},{x:178,y:315},{x:150,y:320},{x:120,y:315},{x:100,y:298},{x:92,y:272},{x:90,y:248}]]},
    {id:"PA",name:"Pennsylvania",poly:[[{x:760,y:310},{x:790,y:305},{x:820,y:308},{x:840,y:315},{x:848,y:332},{x:842,y:350},{x:828,y:362},{x:805,y:368},{x:775,y:365},{x:755,y:355},{x:748,y:338},{x:750,y:322}]]},
    {id:"RI",name:"Rhode Island",poly:[[{x:868,y:305},{x:878,y:302},{x:884,y:308},{x:882,y:318},{x:876,y:325},{x:866,y:322},{x:862,y:314}]]},
    {id:"SC",name:"South Carolina",poly:[[{x:730,y:520},{x:755,y:515},{x:778,y:520},{x:795,y:532},{x:800,y:550},{x:795,y:568},{x:782,y:580},{x:762,y:582},{x:742,y:575},{x:728,y:558},{x:725,y:538}]]},
    {id:"SD",name:"South Dakota",poly:[[{x:400,y:255},{x:440,y:250},{x:485,y:252},{x:508,y:262},{x:515,y:280},{x:510,y:305},{x:498,y:322},{x:475,y:330},{x:440,y:332},{x:412,y:325},{x:398,y:308},{x:395,y:282}]]},
    {id:"TN",name:"Tennessee",poly:[[{x:620,y:490},{x:660,y:485},{x:700,y:488},{x:735,y:492},{x:755,y:500},{x:758,y:515},{x:748,y:528},{x:725,y:535},{x:690,y:538},{x:650,y:535},{x:622,y:528},{x:612,y:512},{x:615,y:498}]]},
    {id:"TX",name:"Texas",poly:[[{x:360,y:530},{x:400,y:520},{x:440,y:525},{x:480,y:535},{x:520,y:545},{x:548,y:560},{x:560,y:585},{x:562,y:615},{x:555,y:650},{x:540,y:685},{x:520,y:715},{x:495,y:735},{x:465,y:742},{x:430,y:738},{x:395,y:725},{x:365,y:705},{x:345,y:678},{x:335,y:645},{x:332,y:610},{x:338,y:575},{x:348,y:548}]]},
    {id:"UT",name:"Utah",poly:[[{x:225,y:350},{x:260,y:345},{x:290,y:348},{x:305,y:362},{x:310,y:385},{x:308,y:415},{x:302,y:448},{x:290,y:468},{x:268,y:475},{x:245,y:468},{x:230,y:448},{x:222,y:420},{x:220,y:388},{x:222,y:365}]]},
    {id:"VT",name:"Vermont",poly:[[{x:852,y:228},{x:862,y:222},{x:870,y:228},{x:872,y:242},{x:868,y:258},{x:860,y:268},{x:850,y:265},{x:845,y:250},{x:848,y:235}]]},
    {id:"VA",name:"Virginia",poly:[[{x:720,y:420},{x:750,y:415},{x:780,y:418},{x:810,y:425},{x:835,y:438},{x:848,y:455},{x:842,y:472},{x:825,y:482},{x:798,y:485},{x:765,y:482},{x:735,y:475},{x:718,y:460},{x:715,y:440}]]},
    {id:"WA",name:"Washington",poly:[[{x:115,y:155},{x:150,y:145},{x:185,y:148},{x:210,y:158},{x:218,y:178},{x:215,y:200},{x:205,y:218},{x:185,y:228},{x:155,y:230},{x:128,y:222},{x:112,y:205},{x:108,y:182}]]},
    {id:"WV",name:"West Virginia",poly:[[{x:740,y:390},{x:758,y:385},{x:772,y:395},{x:778,y:412},{x:775,y:432},{x:765,y:448},{x:750,y:455},{x:735,y:448},{x:728,y:430},{x:730,y:410}]]},
    {id:"WI",name:"Wisconsin",poly:[[{x:555,y:225},{x:580,y:218},{x:605,y:222},{x:622,y:238},{x:630,y:260},{x:628,y:290},{x:620,y:318},{x:605,y:335},{x:582,y:340},{x:560,y:332},{x:545,y:312},{x:540,y:285},{x:542,y:255}]]},
    {id:"WY",name:"Wyoming",poly:[[{x:270,y:260},{x:350,y:260},{x:350,y:340},{x:270,y:340}]]},
    {id:"DC",name:"District of Columbia",poly:[[{x:798,y:398},{x:804,y:395},{x:808,y:400},{x:806,y:406},{x:800,y:408},{x:796,y:403}]]}
  ];

  /* ========================================================================
   *  2.  WORLD COUNTRIES – Robinson-ish projection, 0-1000 coords
   *      Each entry: { id, name, poly: [[{x,y},...], ...] }
   * ======================================================================== */
  const worldCountries = [
    {id:"US",name:"United States",poly:[[{x:120,y:310},{x:155,y:300},{x:195,y:295},{x:235,y:298},{x:265,y:310},{x:275,y:330},{x:270,y:350},{x:255,y:365},{x:230,y:370},{x:195,y:368},{x:160,y:362},{x:135,y:348},{x:120,y:330}]]},
    {id:"CA",name:"Canada",poly:[[{x:115,y:200},{x:155,y:180},{x:200,y:170},{x:250,y:175},{x:290,y:190},{x:310,y:210},{x:305,y:235},{x:285,y:255},{x:255,y:268},{x:215,y:275},{x:170,y:272},{x:135,y:260},{x:115,y:238}]]},
    {id:"MX",name:"Mexico",poly:[[{x:120,y:375},{x:150,y:365},{x:180,y:370},{x:200,y:385},{x:210,y:405},{x:205,y:430},{x:190,y:448},{x:168,y:455},{x:142,y:448},{x:125,y:430},{x:118,y:408},{x:115,y:388}]]},
    {id:"BR",name:"Brazil",poly:[[{x:250,y:470},{x:290,y:455},{x:330,y:458},{x:355,y:475},{x:368,y:500},{x:370,y:535},{x:362,y:570},{x:345,y:600},{x:320,y:620},{x:290,y:628},{x:260,y:618},{x:240,y:595},{x:232,y:560},{x:235,y:525},{x:240,y:492}]]},
    {id:"AR",name:"Argentina",poly:[[{x:240,y:620},{x:260,y:610},{x:278,y:618},{x:290,y:640},{x:295,y:670},{x:288,y:705},{x:275,y:738},{x:258,y:760},{x:242,y:755},{x:230,y:730},{x:225,y:698},{x:228,y:662},{x:232,y:638}]]},
    {id:"CO",name:"Colombia",poly:[[{x:200,y:420},{x:222,y:412},{x:245,y:418},{x:258,y:435},{x:262,y:458},{x:255,y:478},{x:240,y:490},{x:218,y:488},{x:202,y:475},{x:195,y:452},{x:195,y:435}]]},
    {id:"PE",name:"Peru",poly:[[{x:195,y:488},{x:218,y:482},{x:238,y:490},{x:248,y:510},{x:250,y:535},{x:242,y:560},{x:228,y:575},{x:210,y:572},{x:198,y:555},{x:192,y:530},{x:190,y:508}]]},
    {id:"VE",name:"Venezuela",poly:[[{x:222,y:395},{x:248,y:388},{x:270,y:395},{x:282,y:412},{x:278,y:432},{x:265,y:445},{x:245,y:448},{x:225,y:442},{x:215,y:425},{x:215,y:408}]]},
    {id:"CL",name:"Chile",poly:[[{x:225,y:600},{x:238,y:595},{x:245,y:612},{x:248,y:640},{x:245,y:675},{x:240,y:710},{x:232,y:745},{x:225,y:762},{x:218,y:752},{x:215,y:720},{x:218,y:685},{x:220,y:648},{x:222,y:620}]]},
    {id:"GB",name:"United Kingdom",poly:[[{x:440,y:230},{x:448,y:222},{x:455,y:225},{x:458,y:238},{x:455,y:252},{x:448,y:262},{x:440,y:258},{x:436,y:245}]]},
    {id:"FR",name:"France",poly:[[{x:448,y:270},{x:462,y:262},{x:478,y:265},{x:488,y:278},{x:490,y:295},{x:485,y:312},{x:472,y:322},{x:455,y:320},{x:442,y:308},{x:438,y:290},{x:440,y:278}]]},
    {id:"DE",name:"Germany",poly:[[{x:478,y:245},{x:495,y:238},{x:510,y:242},{x:518,y:258},{x:520,y:278},{x:515,y:298},{x:502,y:308},{x:485,y:305},{x:475,y:290},{x:472,y:268}]]},
    {id:"ES",name:"Spain",poly:[[{x:425,y:310},{x:445,y:302},{x:465,y:308},{x:472,y:322},{x:468,y:340},{x:455,y:352},{x:435,y:352},{x:422,y:340},{x:418,y:325}]]},
    {id:"IT",name:"Italy",poly:[[{x:488,y:298},{x:498,y:290},{x:508,y:295},{x:515,y:312},{x:518,y:332},{x:512,y:355},{x:502,y:368},{x:492,y:362},{x:485,y:342},{x:482,y:318}]]},
    {id:"PT",name:"Portugal",poly:[[{x:415,y:312},{x:425,y:308},{x:428,y:318},{x:428,y:335},{x:425,y:350},{x:418,y:355},{x:412,y:345},{x:410,y:328}]]},
    {id:"NO",name:"Norway",poly:[[{x:470,y:155},{x:482,y:145},{x:495,y:148},{x:502,y:165},{x:505,y:188},{x:500,y:212},{x:492,y:228},{x:480,y:235},{x:468,y:225},{x:462,y:205},{x:462,y:180}]]},
    {id:"SE",name:"Sweden",poly:[[{x:490,y:165},{x:502,y:158},{x:512,y:165},{x:518,y:185},{x:518,y:210},{x:512,y:232},{x:502,y:245},{x:490,y:240},{x:485,y:218},{x:484,y:192}]]},
    {id:"FI",name:"Finland",poly:[[{x:518,y:148},{x:530,y:140},{x:542,y:145},{x:548,y:165},{x:548,y:192},{x:542,y:215},{x:532,y:230},{x:520,y:225},{x:514,y:205},{x:512,y:178}]]},
    {id:"PL",name:"Poland",poly:[[{x:508,y:248},{x:525,y:242},{x:540,y:248},{x:548,y:262},{x:548,y:282},{x:540,y:298},{x:525,y:305},{x:510,y:298},{x:505,y:280},{x:504,y:262}]]},
    {id:"UA",name:"Ukraine",poly:[[{x:545,y:258},{x:570,y:250},{x:598,y:255},{x:618,y:268},{x:625,y:288},{x:618,y:308},{x:602,y:318},{x:578,y:320},{x:555,y:312},{x:542,y:295},{x:540,y:275}]]},
    {id:"RO",name:"Romania",poly:[[{x:528,y:295},{x:548,y:288},{x:565,y:295},{x:572,y:310},{x:568,y:328},{x:555,y:338},{x:538,y:338},{x:525,y:325},{x:522,y:308}]]},
    {id:"RU",name:"Russia",poly:[[{x:550,y:120},{x:600,y:105},{x:660,y:100},{x:730,y:108},{x:790,y:118},{x:835,y:135},{x:855,y:160},{x:850,y:190},{x:830,y:218},{x:798,y:238},{x:755,y:250},{x:705,y:255},{x:650,y:248},{x:605,y:238},{x:572,y:222},{x:555,y:195},{x:545,y:160}]]},
    {id:"CN",name:"China",poly:[[{x:720,y:285},{x:755,y:275},{x:790,y:280},{x:820,y:292},{x:840,y:312},{x:848,y:340},{x:842,y:370},{x:828,y:395},{x:805,y:410},{x:775,y:415},{x:742,y:408},{x:718,y:392},{x:705,y:368},{x:700,y:340},{x:705,y:310}]]},
    {id:"JP",name:"Japan",poly:[[{x:862,y:295},{x:870,y:288},{x:878,y:292},{x:882,y:308},{x:880,y:328},{x:875,y:345},{x:868,y:355},{x:860,y:348},{x:856,y:328},{x:858,y:310}]]},
    {id:"IN",name:"India",poly:[[{x:680,y:370},{x:705,y:360},{x:728,y:368},{x:742,y:388},{x:748,y:415},{x:745,y:445},{x:735,y:472},{x:718,y:490},{x:698,y:492},{x:680,y:480},{x:670,y:458},{x:665,y:430},{x:668,y:400},{x:672,y:382}]]},
    {id:"KR",name:"South Korea",poly:[[{x:848,y:310},{x:855,y:305},{x:862,y:310},{x:865,y:322},{x:862,y:335},{x:855,y:342},{x:848,y:338},{x:845,y:325}]]},
    {id:"ID",name:"Indonesia",poly:[[{x:770,y:455},{x:795,y:448},{x:825,y:452},{x:852,y:460},{x:865,y:472},{x:860,y:488},{x:842,y:498},{x:815,y:502},{x:785,y:498},{x:762,y:488},{x:758,y:472}]]},
    {id:"TH",name:"Thailand",poly:[[{x:755,y:398},{x:765,y:390},{x:775,y:395},{x:780,y:410},{x:778,y:430},{x:772,y:448},{x:762,y:455},{x:752,y:448},{x:748,y:430},{x:750,y:412}]]},
    {id:"VN",name:"Vietnam",poly:[[{x:778,y:388},{x:788,y:382},{x:795,y:390},{x:798,y:408},{x:795,y:430},{x:790,y:450},{x:782,y:460},{x:775,y:452},{x:772,y:432},{x:775,y:408}]]},
    {id:"PH",name:"Philippines",poly:[[{x:832,y:395},{x:840,y:388},{x:848,y:392},{x:852,y:408},{x:850,y:428},{x:845,y:442},{x:835,y:448},{x:828,y:438},{x:825,y:418},{x:828,y:402}]]},
    {id:"MY",name:"Malaysia",poly:[[{x:782,y:448},{x:795,y:442},{x:808,y:448},{x:815,y:460},{x:812,y:475},{x:802,y:482},{x:788,y:480},{x:778,y:468},{x:778,y:455}]]},
    {id:"PK",name:"Pakistan",poly:[[{x:648,y:338},{x:668,y:330},{x:685,y:335},{x:695,y:352},{x:698,y:375},{x:690,y:395},{x:675,y:405},{x:655,y:400},{x:642,y:382},{x:640,y:358}]]},
    {id:"BD",name:"Bangladesh",poly:[[{x:728,y:385},{x:740,y:380},{x:750,y:388},{x:752,y:402},{x:748,y:415},{x:738,y:422},{x:728,y:415},{x:724,y:400}]]},
    {id:"TR",name:"Turkey",poly:[[{x:545,y:315},{x:570,y:308},{x:598,y:312},{x:620,y:322},{x:632,y:338},{x:625,y:355},{x:608,y:365},{x:582,y:368},{x:558,y:362},{x:542,y:345},{x:538,y:328}]]},
    {id:"IR",name:"Iran",poly:[[{x:618,y:328},{x:642,y:320},{x:665,y:325},{x:680,y:342},{x:685,y:365},{x:678,y:388},{x:662,y:400},{x:640,y:398},{x:622,y:385},{x:615,y:362},{x:612,y:342}]]},
    {id:"SA",name:"Saudi Arabia",poly:[[{x:580,y:372},{x:608,y:365},{x:635,y:372},{x:652,y:390},{x:658,y:415},{x:650,y:440},{x:632,y:455},{x:608,y:458},{x:585,y:448},{x:572,y:428},{x:570,y:400},{x:575,y:382}]]},
    {id:"IQ",name:"Iraq",poly:[[{x:588,y:338},{x:608,y:332},{x:622,y:340},{x:628,y:358},{x:625,y:378},{x:615,y:390},{x:600,y:392},{x:585,y:382},{x:580,y:362},{x:582,y:348}]]},
    {id:"EG",name:"Egypt",poly:[[{x:530,y:368},{x:550,y:360},{x:568,y:365},{x:578,y:380},{x:580,y:402},{x:575,y:422},{x:562,y:435},{x:545,y:432},{x:530,y:420},{x:525,y:398},{x:525,y:380}]]},
    {id:"NG",name:"Nigeria",poly:[[{x:472,y:428},{x:492,y:420},{x:512,y:425},{x:522,y:442},{x:522,y:462},{x:515,y:478},{x:500,y:488},{x:480,y:485},{x:468,y:470},{x:465,y:450}]]},
    {id:"ET",name:"Ethiopia",poly:[[{x:565,y:432},{x:585,y:425},{x:605,y:430},{x:615,y:445},{x:618,y:465},{x:610,y:482},{x:595,y:492},{x:575,y:488},{x:562,y:472},{x:558,y:452}]]},
    {id:"KE",name:"Kenya",poly:[[{x:572,y:468},{x:588,y:462},{x:602,y:468},{x:608,y:482},{x:608,y:500},{x:600,y:515},{x:588,y:520},{x:575,y:512},{x:568,y:495},{x:568,y:480}]]},
    {id:"ZA",name:"South Africa",poly:[[{x:505,y:590},{x:530,y:580},{x:555,y:585},{x:572,y:600},{x:578,y:622},{x:572,y:645},{x:558,y:660},{x:535,y:665},{x:512,y:658},{x:498,y:640},{x:495,y:618},{x:498,y:600}]]},
    {id:"DZ",name:"Algeria",poly:[[{x:445,y:340},{x:468,y:332},{x:490,y:338},{x:502,y:355},{x:505,y:378},{x:498,y:402},{x:485,y:418},{x:465,y:420},{x:445,y:412},{x:435,y:392},{x:432,y:368},{x:435,y:350}]]},
    {id:"MA",name:"Morocco",poly:[[{x:418,y:338},{x:438,y:330},{x:452,y:338},{x:455,y:355},{x:450,y:375},{x:440,y:390},{x:425,y:392},{x:415,y:378},{x:412,y:358}]]},
    {id:"TN",name:"Tunisia",poly:[[{x:478,y:322},{x:488,y:318},{x:495,y:325},{x:498,y:340},{x:495,y:355},{x:488,y:362},{x:478,y:358},{x:475,y:342}]]},
    {id:"LY",name:"Libya",poly:[[{x:498,y:355},{x:518,y:348},{x:538,y:352},{x:550,y:368},{x:552,y:390},{x:545,y:412},{x:532,y:425},{x:512,y:428},{x:498,y:418},{x:490,y:398},{x:488,y:375}]]},
    {id:"GH",name:"Ghana",poly:[[{x:452,y:432},{x:462,y:428},{x:470,y:435},{x:472,y:450},{x:468,y:465},{x:460,y:475},{x:450,y:470},{x:448,y:455},{x:448,y:442}]]},
    {id:"CI",name:"Ivory Coast",poly:[[{x:438,y:435},{x:450,y:430},{x:458,y:438},{x:460,y:455},{x:455,y:470},{x:445,y:478},{x:435,y:472},{x:432,y:455},{x:435,y:442}]]},
    {id:"CM",name:"Cameroon",poly:[[{x:488,y:438},{x:500,y:432},{x:510,y:440},{x:515,y:458},{x:512,y:478},{x:502,y:490},{x:490,y:488},{x:484,y:470},{x:484,y:452}]]},
    {id:"CD",name:"Congo (DRC)",poly:[[{x:518,y:468},{x:540,y:460},{x:562,y:465},{x:575,y:482},{x:578,y:505},{x:572,y:530},{x:558,y:548},{x:538,y:552},{x:518,y:542},{x:508,y:522},{x:505,y:498},{x:508,y:478}]]},
    {id:"TZ",name:"Tanzania",poly:[[{x:558,y:490},{x:575,y:482},{x:590,y:490},{x:598,y:508},{x:598,y:530},{x:590,y:548},{x:578,y:558},{x:562,y:552},{x:552,y:535},{x:550,y:512}]]},
    {id:"AO",name:"Angola",poly:[[{x:495,y:498},{x:515,y:490},{x:535,y:495},{x:545,y:512},{x:548,y:535},{x:542,y:558},{x:528,y:572},{x:510,y:568},{x:498,y:550},{x:492,y:528},{x:490,y:510}]]},
    {id:"MZ",name:"Mozambique",poly:[[{x:562,y:530},{x:575,y:525},{x:585,y:535},{x:590,y:555},{x:588,y:578},{x:580,y:598},{x:570,y:608},{x:558,y:600},{x:552,y:575},{x:552,y:550}]]},
    {id:"AU",name:"Australia",poly:[[{x:800,y:540},{x:840,y:525},{x:880,y:530},{x:910,y:545},{x:928,y:568},{x:930,y:598},{x:920,y:628},{x:900,y:650},{x:872,y:662},{x:838,y:665},{x:808,y:655},{x:788,y:635},{x:778,y:608},{x:780,y:575}]]},
    {id:"NZ",name:"New Zealand",poly:[[{x:935,y:628},{x:945,y:620},{x:952,y:628},{x:955,y:645},{x:950,y:665},{x:942,y:680},{x:935,y:675},{x:930,y:655},{x:932,y:640}]]},
    {id:"KZ",name:"Kazakhstan",poly:[[{x:618,y:248},{x:650,y:238},{x:688,y:242},{x:715,y:255},{x:725,y:275},{x:718,y:298},{x:700,y:312},{x:672,y:318},{x:642,y:312},{x:622,y:295},{x:615,y:272}]]},
    {id:"UZ",name:"Uzbekistan",poly:[[{x:635,y:288},{x:655,y:282},{x:672,y:288},{x:680,y:302},{x:678,y:320},{x:668,y:332},{x:652,y:335},{x:638,y:325},{x:632,y:308}]]},
    {id:"AF",name:"Afghanistan",poly:[[{x:658,y:318},{x:678,y:312},{x:695,y:318},{x:705,y:335},{x:702,y:355},{x:692,y:368},{x:675,y:372},{x:658,y:362},{x:650,y:342}]]},
    {id:"MM",name:"Myanmar",poly:[[{x:745,y:378},{x:758,y:372},{x:768,y:380},{x:772,y:398},{x:770,y:420},{x:762,y:440},{x:752,y:448},{x:742,y:438},{x:738,y:415},{x:740,y:395}]]},
    {id:"SD",name:"Sudan",poly:[[{x:545,y:398},{x:568,y:390},{x:588,y:398},{x:598,y:418},{x:600,y:445},{x:592,y:468},{x:578,y:480},{x:558,y:478},{x:542,y:462},{x:538,y:435},{x:540,y:412}]]},
    {id:"SN",name:"Senegal",poly:[[{x:408,y:412},{x:420,y:408},{x:430,y:415},{x:432,y:428},{x:428,y:440},{x:418,y:445},{x:408,y:438},{x:405,y:425}]]},
    {id:"ML",name:"Mali",poly:[[{x:432,y:388},{x:455,y:380},{x:475,y:388},{x:485,y:405},{x:485,y:428},{x:478,y:445},{x:462,y:452},{x:442,y:448},{x:430,y:432},{x:428,y:412}]]},
    {id:"NE",name:"Niger",poly:[[{x:472,y:385},{x:495,y:378},{x:518,y:382},{x:530,y:398},{x:532,y:418},{x:525,y:438},{x:510,y:448},{x:490,y:445},{x:475,y:432},{x:468,y:412},{x:468,y:395}]]},
    {id:"TD",name:"Chad",poly:[[{x:510,y:395},{x:530,y:388},{x:548,y:395},{x:558,y:412},{x:558,y:435},{x:550,y:455},{x:535,y:465},{x:518,y:460},{x:508,y:442},{x:505,y:418}]]},
    {id:"MG",name:"Madagascar",poly:[[{x:592,y:542},{x:602,y:535},{x:610,y:542},{x:615,y:562},{x:612,y:588},{x:605,y:608},{x:595,y:615},{x:588,y:602},{x:585,y:575},{x:588,y:555}]]},
    {id:"EC",name:"Ecuador",poly:[[{x:185,y:455},{x:198,y:450},{x:210,y:458},{x:215,y:472},{x:210,y:488},{x:200,y:495},{x:188,y:490},{x:182,y:475}]]},
    {id:"BO",name:"Bolivia",poly:[[{x:245,y:530},{x:268,y:522},{x:288,y:528},{x:298,y:548},{x:300,y:575},{x:292,y:598},{x:278,y:610},{x:258,y:608},{x:242,y:592},{x:238,y:565},{x:240,y:542}]]},
    {id:"PY",name:"Paraguay",poly:[[{x:268,y:570},{x:285,y:565},{x:298,y:575},{x:302,y:592},{x:298,y:610},{x:288,y:620},{x:275,y:618},{x:265,y:605},{x:262,y:588}]]},
    {id:"UY",name:"Uruguay",poly:[[{x:282,y:618},{x:295,y:612},{x:305,y:620},{x:308,y:638},{x:302,y:652},{x:292,y:658},{x:280,y:650},{x:278,y:635}]]},
    {id:"GY",name:"Guyana",poly:[[{x:262,y:418},{x:275,y:412},{x:285,y:420},{x:288,y:435},{x:282,y:448},{x:272,y:452},{x:260,y:445},{x:258,y:430}]]},
    {id:"AE",name:"UAE",poly:[[{x:628,y:398},{x:640,y:392},{x:650,y:398},{x:652,y:412},{x:648,y:425},{x:638,y:430},{x:628,y:422},{x:625,y:410}]]},
    {id:"IL",name:"Israel",poly:[[{x:558,y:348},{x:565,y:342},{x:570,y:348},{x:572,y:362},{x:568,y:375},{x:562,y:380},{x:556,y:372},{x:555,y:358}]]},
    {id:"SY",name:"Syria",poly:[[{x:565,y:322},{x:580,y:318},{x:592,y:325},{x:598,y:340},{x:595,y:358},{x:585,y:368},{x:572,y:365},{x:562,y:350},{x:560,y:335}]]},
    {id:"JO",name:"Jordan",poly:[[{x:562,y:355},{x:572,y:350},{x:580,y:358},{x:582,y:372},{x:578,y:385},{x:570,y:390},{x:560,y:382},{x:558,y:368}]]},
    {id:"LK",name:"Sri Lanka",poly:[[{x:712,y:462},{x:720,y:458},{x:725,y:465},{x:725,y:478},{x:720,y:488},{x:712,y:485},{x:708,y:475}]]},
    {id:"NP",name:"Nepal",poly:[[{x:710,y:362},{x:728,y:358},{x:742,y:365},{x:745,y:378},{x:738,y:390},{x:722,y:392},{x:710,y:385},{x:708,y:372}]]},
    {id:"KH",name:"Cambodia",poly:[[{x:775,y:420},{x:788,y:415},{x:798,y:422},{x:800,y:438},{x:795,y:450},{x:785,y:455},{x:775,y:448},{x:772,y:435}]]},
    {id:"LA",name:"Laos",poly:[[{x:768,y:392},{x:778,y:385},{x:788,y:392},{x:792,y:408},{x:788,y:422},{x:780,y:430},{x:770,y:425},{x:765,y:410}]]},
    {id:"MN",name:"Mongolia",poly:[[{x:730,y:255},{x:758,y:248},{x:788,y:252},{x:808,y:265},{x:815,y:282},{x:808,y:300},{x:792,y:310},{x:765,y:312},{x:740,y:305},{x:728,y:288},{x:725,y:270}]]},
    {id:"KP",name:"North Korea",poly:[[{x:838,y:288},{x:850,y:282},{x:860,y:288},{x:862,y:302},{x:858,y:315},{x:848,y:322},{x:838,y:318},{x:835,y:305}]]},
    {id:"PG",name:"Papua New Guinea",poly:[[{x:892,y:468},{x:908,y:462},{x:922,y:468},{x:928,y:482},{x:925,y:498},{x:915,y:508},{x:900,y:505},{x:888,y:492},{x:888,y:478}]]},
    {id:"CU",name:"Cuba",poly:[[{x:195,y:378},{x:215,y:372},{x:235,y:375},{x:245,y:385},{x:242,y:398},{x:228,y:405},{x:210,y:405},{x:195,y:398},{x:190,y:388}]]},
    {id:"HT",name:"Haiti",poly:[[{x:238,y:388},{x:250,y:385},{x:258,y:392},{x:258,y:402},{x:252,y:410},{x:240,y:408},{x:235,y:398}]]},
    {id:"DO",name:"Dominican Republic",poly:[[{x:255,y:388},{x:268,y:385},{x:275,y:392},{x:275,y:405},{x:268,y:412},{x:258,y:408},{x:252,y:398}]]},
    {id:"GT",name:"Guatemala",poly:[[{x:155,y:402},{x:168,y:398},{x:178,y:405},{x:180,y:418},{x:175,y:428},{x:162,y:432},{x:152,y:425},{x:150,y:412}]]},
    {id:"CR",name:"Costa Rica",poly:[[{x:178,y:428},{x:188,y:425},{x:195,y:432},{x:195,y:442},{x:190,y:450},{x:180,y:448},{x:175,y:440}]]},
    {id:"PA2",name:"Panama",poly:[[{x:192,y:438},{x:205,y:435},{x:215,y:442},{x:215,y:455},{x:208,y:462},{x:198,y:458},{x:190,y:450}]]},
    {id:"IE",name:"Ireland",poly:[[{x:428,y:232},{x:438,y:228},{x:445,y:235},{x:445,y:248},{x:438,y:255},{x:430,y:252},{x:425,y:242}]]},
    {id:"NL",name:"Netherlands",poly:[[{x:465,y:252},{x:475,y:248},{x:480,y:255},{x:480,y:265},{x:475,y:272},{x:468,y:268},{x:462,y:260}]]},
    {id:"BE",name:"Belgium",poly:[[{x:462,y:265},{x:472,y:262},{x:478,y:268},{x:478,y:278},{x:472,y:285},{x:465,y:282},{x:460,y:275}]]},
    {id:"CH",name:"Switzerland",poly:[[{x:472,y:282},{x:482,y:278},{x:490,y:285},{x:490,y:295},{x:484,y:302},{x:475,y:298},{x:470,y:290}]]},
    {id:"AT",name:"Austria",poly:[[{x:490,y:272},{x:505,y:268},{x:515,y:275},{x:518,y:288},{x:512,y:298},{x:498,y:300},{x:488,y:292},{x:486,y:280}]]},
    {id:"CZ",name:"Czech Republic",poly:[[{x:492,y:255},{x:508,y:250},{x:520,y:258},{x:522,y:272},{x:515,y:282},{x:500,y:285},{x:490,y:275},{x:488,y:262}]]},
    {id:"HU",name:"Hungary",poly:[[{x:510,y:280},{x:525,y:275},{x:538,y:282},{x:542,y:295},{x:535,y:308},{x:522,y:312},{x:510,y:305},{x:506,y:292}]]},
    {id:"GR",name:"Greece",poly:[[{x:520,y:328},{x:532,y:322},{x:542,y:328},{x:545,y:345},{x:540,y:362},{x:532,y:372},{x:520,y:368},{x:515,y:352},{x:515,y:338}]]},
    {id:"BG",name:"Bulgaria",poly:[[{x:530,y:308},{x:545,y:302},{x:555,y:310},{x:558,y:325},{x:552,y:338},{x:540,y:342},{x:528,y:335},{x:525,y:320}]]},
    {id:"RS",name:"Serbia",poly:[[{x:515,y:302},{x:528,y:298},{x:538,y:305},{x:540,y:320},{x:535,y:332},{x:525,y:338},{x:515,y:330},{x:512,y:315}]]},
    {id:"HR",name:"Croatia",poly:[[{x:500,y:295},{x:515,y:290},{x:525,y:298},{x:525,y:315},{x:518,y:328},{x:505,y:330},{x:498,y:318},{x:496,y:305}]]},
    {id:"DK",name:"Denmark",poly:[[{x:475,y:228},{x:485,y:224},{x:492,y:230},{x:492,y:242},{x:488,y:252},{x:478,y:252},{x:472,y:242},{x:472,y:234}]]},
    {id:"SK",name:"Slovakia",poly:[[{x:515,y:265},{x:530,y:260},{x:540,y:268},{x:542,y:280},{x:535,y:290},{x:522,y:292},{x:512,y:282},{x:512,y:272}]]},
    {id:"BA",name:"Bosnia",poly:[[{x:505,y:305},{x:515,y:300},{x:522,y:308},{x:522,y:322},{x:515,y:330},{x:508,y:325},{x:502,y:315}]]},
    {id:"AL2",name:"Albania",poly:[[{x:515,y:330},{x:522,y:325},{x:528,y:332},{x:528,y:345},{x:522,y:352},{x:515,y:348},{x:512,y:340}]]},
    {id:"MK",name:"North Macedonia",poly:[[{x:522,y:322},{x:532,y:318},{x:538,y:325},{x:538,y:338},{x:532,y:345},{x:525,y:340},{x:520,y:332}]]},
    {id:"SI",name:"Slovenia",poly:[[{x:495,y:282},{x:505,y:278},{x:512,y:285},{x:512,y:295},{x:505,y:302},{x:498,y:298},{x:494,y:290}]]},
    {id:"LT",name:"Lithuania",poly:[[{x:520,y:230},{x:532,y:226},{x:540,y:232},{x:542,y:245},{x:536,y:255},{x:525,y:255},{x:518,y:245},{x:518,y:236}]]},
    {id:"LV",name:"Latvia",poly:[[{x:520,y:218},{x:535,y:214},{x:542,y:222},{x:542,y:235},{x:536,y:245},{x:525,y:245},{x:518,y:235},{x:518,y:225}]]},
    {id:"EE",name:"Estonia",poly:[[{x:525,y:205},{x:538,y:200},{x:545,y:208},{x:545,y:220},{x:538,y:228},{x:528,y:228},{x:522,y:218},{x:522,y:210}]]}
  ];

  /* ========================================================================
   *  3.  RENDERING HELPERS
   * ======================================================================== */

  const CYAN = {r:6, g:182, b:212};        // #06b6d4
  const CYAN_LIGHT = {r:103, g:232, b:249};// #67e8f9
  const FONT = "'Outfit', sans-serif";

  function lerp(a, b, t) { return a + (b - a) * t; }

  function cyanFill(t) {
    // t in [0,1] → rgba cyan with opacity 0.12 .. 0.85
    const opacity = lerp(0.12, 0.85, t);
    const r = Math.round(lerp(CYAN.r, CYAN_LIGHT.r, t));
    const g = Math.round(lerp(CYAN.g, CYAN_LIGHT.g, t));
    const b = Math.round(lerp(CYAN.b, CYAN_LIGHT.b, t));
    return `rgba(${r},${g},${b},${opacity})`;
  }

  /** Normalise a dataMap (string-keyed) into { key→{norm,raw} } using min/max */
  function normalise(dataMap) {
    const vals = Object.values(dataMap).filter(v => typeof v === "number");
    if (!vals.length) return {};
    const mn = Math.min(...vals);
    const mx = Math.max(...vals);
    const range = mx - mn || 1;
    const out = {};
    for (const [k, v] of Object.entries(dataMap)) {
      if (typeof v !== "number") continue;
      out[k] = { norm: (v - mn) / range, raw: v };
    }
    return out;
  }

  /** Build a lookup from name/id → normalised entry */
  function buildLookup(regions, normMap) {
    const lk = {};
    for (const r of regions) {
      const key =
        normMap[r.name] !== undefined ? r.name :
        normMap[r.id]   !== undefined ? r.id   : null;
      if (key !== null) lk[r.id] = normMap[key];
    }
    return lk;
  }

  /** Point-in-polygon (ray casting) for a single ring */
  function pointInPoly(px, py, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const xi = ring[i].x, yi = ring[i].y;
      const xj = ring[j].x, yj = ring[j].y;
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) {
        inside = !inside;
      }
    }
    return inside;
  }

  /** Format number for tooltips */
  function fmt(v) {
    if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return v.toLocaleString();
  }

  /* ========================================================================
   *  4.  drawUSMapChart
   * ======================================================================== */
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {Object} dataMap  – { stateName|stateId : number }
   * @param {Object} [opts]
   * @param {string} [opts.title]
   * @param {string} [opts.valueLabel]
   * @param {{x:number,y:number}|null} [opts.mouse]  – current mouse pos (canvas-relative)
   * @returns {{ hovered: string|null }}
   */
  function drawUSMapChart(canvas, dataMap, opts) {
    opts = opts || {};
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    const dpr = window.devicePixelRatio || 1;

    // scale factors: map coords are 0-1000 → fit into canvas with padding
    const pad = 40 * dpr;
    const mapW = W - pad * 2;
    const mapH = H - pad * 2 - 60 * dpr; // room for legend
    const sx = mapW / 1000;
    const sy = mapH / 900; // US data spans ~150-920 in y
    const scale = Math.min(sx, sy);
    const ox = pad + (mapW - 1000 * scale) / 2;
    const oy = pad + 30 * dpr;

    // background
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);

    // normalise data
    const normMap = normalise(dataMap);
    const lk = buildLookup(usStates, normMap);

    // mouse hit
    let hovered = null;
    const mx = opts.mouse ? opts.mouse.x : -9999;
    const my = opts.mouse ? opts.mouse.y : -9999;

    // draw states
    for (const st of usStates) {
      for (const ring of st.poly) {
        ctx.beginPath();
        for (let i = 0; i < ring.length; i++) {
          const px = ox + ring[i].x * scale;
          const py = oy + ring[i].y * scale;
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();

        // hit test
        const scaledRing = ring.map(p => ({ x: ox + p.x * scale, y: oy + p.y * scale }));
        if (pointInPoly(mx, my, scaledRing)) hovered = st.name;

        // fill
        const entry = lk[st.id];
        if (entry) {
          ctx.fillStyle = cyanFill(entry.norm);
        } else {
          ctx.fillStyle = "rgba(255,255,255,0.03)";
        }
        ctx.fill();

        // border
        ctx.strokeStyle = hovered === st.name ? "rgba(103,232,249,0.6)" : "rgba(255,255,255,0.06)";
        ctx.lineWidth = hovered === st.name ? 2 * dpr : 1 * dpr;
        ctx.stroke();
      }
    }

    // title
    if (opts.title) {
      ctx.fillStyle = "rgba(255,255,255,0.80)";
      ctx.font = `300 ${14 * dpr}px ${FONT}`;
      ctx.textAlign = "center";
      ctx.fillText(opts.title, W / 2, pad / 2 + 8 * dpr);
    }

    // tooltip
    if (hovered && opts.mouse) {
      const entry = lk[usStates.find(s => s.name === hovered).id];
      const label = hovered + (entry ? ": " + fmt(entry.raw) + (opts.valueLabel ? " " + opts.valueLabel : "") : "");
      ctx.font = `300 ${11 * dpr}px ${FONT}`;
      const tw = ctx.measureText(label).width + 16 * dpr;
      const th = 24 * dpr;
      const tx = Math.min(mx + 12 * dpr, W - tw - 4);
      const ty = Math.max(my - th - 4, 4);
      ctx.fillStyle = "rgba(0,0,0,0.85)";
      ctx.strokeStyle = "rgba(103,232,249,0.3)";
      ctx.lineWidth = 1 * dpr;
      ctx.beginPath();
      ctx.roundRect(tx, ty, tw, th, 6 * dpr);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.90)";
      ctx.textAlign = "left";
      ctx.fillText(label, tx + 8 * dpr, ty + 16 * dpr);
    }

    // ring dot legend
    _drawLegend(ctx, W, H, normMap, dpr, opts.valueLabel);

    return { hovered };
  }

  /* ========================================================================
   *  5.  drawWorldMapChart
   * ======================================================================== */
  function drawWorldMapChart(canvas, dataMap, opts) {
    opts = opts || {};
    const ctx = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    const dpr = window.devicePixelRatio || 1;

    const pad = 40 * dpr;
    const mapW = W - pad * 2;
    const mapH = H - pad * 2 - 60 * dpr;
    const sx = mapW / 1000;
    const sy = mapH / 800;
    const scale = Math.min(sx, sy);
    const ox = pad + (mapW - 1000 * scale) / 2;
    const oy = pad + 20 * dpr;

    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);

    const normMap = normalise(dataMap);
    const lk = buildLookup(worldCountries, normMap);

    let hovered = null;
    const mx = opts.mouse ? opts.mouse.x : -9999;
    const my = opts.mouse ? opts.mouse.y : -9999;

    for (const ct of worldCountries) {
      for (const ring of ct.poly) {
        ctx.beginPath();
        for (let i = 0; i < ring.length; i++) {
          const px = ox + ring[i].x * scale;
          const py = oy + ring[i].y * scale;
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.closePath();

        const scaledRing = ring.map(p => ({ x: ox + p.x * scale, y: oy + p.y * scale }));
        if (pointInPoly(mx, my, scaledRing)) hovered = ct.name;

        const entry = lk[ct.id];
        if (entry) {
          ctx.fillStyle = cyanFill(entry.norm);
        } else {
          ctx.fillStyle = "rgba(255,255,255,0.03)";
        }
        ctx.fill();

        ctx.strokeStyle = hovered === ct.name ? "rgba(103,232,249,0.6)" : "rgba(255,255,255,0.06)";
        ctx.lineWidth = hovered === ct.name ? 2 * dpr : 1 * dpr;
        ctx.stroke();
      }
    }

    if (opts.title) {
      ctx.fillStyle = "rgba(255,255,255,0.80)";
      ctx.font = `300 ${14 * dpr}px ${FONT}`;
      ctx.textAlign = "center";
      ctx.fillText(opts.title, W / 2, pad / 2 + 8 * dpr);
    }

    if (hovered && opts.mouse) {
      const ct = worldCountries.find(c => c.name === hovered);
      const entry = lk[ct.id];
      const label = hovered + (entry ? ": " + fmt(entry.raw) + (opts.valueLabel ? " " + opts.valueLabel : "") : "");
      ctx.font = `300 ${11 * dpr}px ${FONT}`;
      const tw = ctx.measureText(label).width + 16 * dpr;
      const th = 24 * dpr;
      const tx = Math.min(mx + 12 * dpr, W - tw - 4);
      const ty = Math.max(my - th - 4, 4);
      ctx.fillStyle = "rgba(0,0,0,0.85)";
      ctx.strokeStyle = "rgba(103,232,249,0.3)";
      ctx.lineWidth = 1 * dpr;
      ctx.beginPath();
      ctx.roundRect(tx, ty, tw, th, 6 * dpr);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.90)";
      ctx.textAlign = "left";
      ctx.fillText(label, tx + 8 * dpr, ty + 16 * dpr);
    }

    _drawLegend(ctx, W, H, normMap, dpr, opts.valueLabel);

    return { hovered };
  }

  /* ========================================================================
   *  6.  SHARED LEGEND  (ring dots)
   * ======================================================================== */
  function _drawLegend(ctx, W, H, normMap, dpr, valueLabel) {
    const vals = Object.values(normMap).map(e => e.raw).sort((a, b) => a - b);
    if (!vals.length) return;
    const mn = vals[0];
    const mx = vals[vals.length - 1];
    const steps = 5;
    const dotR = 5 * dpr;
    const gap = 18 * dpr;
    const totalW = steps * (dotR * 2 + gap);
    const startX = (W - totalW) / 2;
    const y = H - 28 * dpr;

    ctx.font = `300 ${9 * dpr}px ${FONT}`;
    ctx.textAlign = "center";

    for (let i = 0; i < steps; i++) {
      const t = i / (steps - 1);
      const cx = startX + i * (dotR * 2 + gap) + dotR;

      // ring
      ctx.beginPath();
      ctx.arc(cx, y, dotR, 0, Math.PI * 2);
      ctx.fillStyle = cyanFill(t);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.15)";
      ctx.lineWidth = 1 * dpr;
      ctx.stroke();

      // label
      const v = lerp(mn, mx, t);
      ctx.fillStyle = "rgba(255,255,255,0.50)";
      ctx.fillText(fmt(v), cx, y + dotR + 10 * dpr);
    }

    if (valueLabel) {
      ctx.fillStyle = "rgba(255,255,255,0.35)";
      ctx.font = `300 ${8 * dpr}px ${FONT}`;
      ctx.textAlign = "center";
      ctx.fillText(valueLabel, W / 2, H - 4 * dpr);
    }
  }

  /* ========================================================================
   *  7.  EXPORT
   * ======================================================================== */
  window.GenBIMaps = {
    usStates,
    worldCountries,
    drawUSMapChart,
    drawWorldMapChart
  };

})();
