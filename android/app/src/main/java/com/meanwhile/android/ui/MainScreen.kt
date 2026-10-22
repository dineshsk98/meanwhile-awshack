// The app's main Compose screen: navigation between the tabs and screens, the Ask sheet, speech, the wake word and deep links.
package com.meanwhile.android.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsTopHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Timeline
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.meanwhile.android.BuildConfig
import com.meanwhile.android.MeanwhileApplication
import com.meanwhile.android.model.ChatMessage
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.foundation.layout.Column
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.compose.ui.platform.LocalContext
import com.meanwhile.android.model.MessageSender
import com.meanwhile.android.ui.ask.AskBottomSheet
import com.meanwhile.android.ui.cameras.CamerasScreen
import com.meanwhile.android.ui.connect.ConnectScreen
import com.meanwhile.android.ui.components.WakeWordChip
import com.meanwhile.android.ui.event.EventDetailScreen
import com.meanwhile.android.ui.home.HomeScreen
import com.meanwhile.android.ui.navigation.Screen
import com.meanwhile.android.util.SpeechController
import com.meanwhile.android.util.WakeWordListener
import com.meanwhile.android.util.SpeechPolicy
import com.meanwhile.android.ui.onboarding.TutorialContent
import com.meanwhile.android.ui.onboarding.TutorialScreen
import androidx.activity.compose.BackHandler
import com.meanwhile.android.ui.people.PeopleScreen
import com.meanwhile.android.ui.sessions.PastSessionsScreen
import com.meanwhile.android.ui.settings.SettingsScreen
import com.meanwhile.android.ui.timeline.TimelineScreen
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal
import androidx.compose.runtime.saveable.rememberSaveable
import com.meanwhile.android.DeepLinkAction
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** The app shell: top bar, bottom tabs, navigation, the pinned Ask bar and sheet, speech, the wake word, the tutorial and deep links. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    navController: NavHostController = rememberNavController(),
    deepLinkAction: DeepLinkAction? = null,
    onDeepLinkConsumed: (Long) -> Unit = {}
) {
    val context = LocalContext.current
    val app = MeanwhileApplication.instance
    val repo = app.repository
    val prefs = app.preferencesManager
    val coroutineScope = rememberCoroutineScope()
    val snackbarHostState = remember { SnackbarHostState() }

    // Remembered so recomposition doesn't restart the pollers
    val activeSessionFlow = remember(repo) { repo.observeActiveSession() }
    val activeSession by activeSessionFlow.collectAsState(initial = null)

    val currentSessionId = activeSession?.id ?: ""
    val eventsFlow = remember(repo, currentSessionId) { repo.observeEvents(currentSessionId) }
    val events by eventsFlow.collectAsState(initial = emptyList())

    val camerasFlow = remember(repo) { repo.observeCameras() }
    val cameras by camerasFlow.collectAsState(initial = emptyList())

    val peopleFlow = remember(repo) { repo.observePeople() }
    val people by peopleFlow.collectAsState(initial = emptyList())

    val pastSessionsFlow = remember(repo) { repo.observePastSessions() }
    val pastSessions by pastSessionsFlow.collectAsState(initial = emptyList())

    val authErrorFlow = remember(repo) { repo.authError }
    val isUnauthorized by authErrorFlow.collectAsState(initial = false)

    // Seconds behind live, null when the core doesn't say
    var lagSeconds by remember { mutableStateOf<Int?>(null) }
    LaunchedEffect(repo) {
        while (true) {
            lagSeconds = repo.getLagSeconds()
            delay(5000)
        }
    }

    // Speech recognition for the mic button
    val speechRecognizer = remember {
        if (SpeechRecognizer.isRecognitionAvailable(context)) {
            SpeechRecognizer.createSpeechRecognizer(context)
        } else null
    }

    DisposableEffect(speechRecognizer) {
        onDispose {
            speechRecognizer?.destroy()
        }
    }

    // First-run tutorial: opens by itself until seen, and again on request
    var showTutorial by rememberSaveable { mutableStateOf(TutorialContent.shouldAutoShow(prefs.tutorialSeen)) }

    // Ask sheet
    var isAskSheetOpen by remember { mutableStateOf(false) }
    var isListening by remember { mutableStateOf(false) }
    var isProcessingQuery by remember { mutableStateOf(false) }
    var hasMicPermission by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
    }
    val askSheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    val chatMessages = remember {
        mutableStateListOf(
            ChatMessage(
                sender = MessageSender.MEANWHILE,
                text = "Hello! Tell me what to keep an eye on, or ask about the house."
            )
        )
    }

    // Spoken replies: one TextToSpeech engine for the screen, muted via a remembered preference
    val speechController = remember { SpeechController(context) }
    var speakReplies by remember { mutableStateOf(prefs.speakReplies) }
    DisposableEffect(speechController) {
        onDispose { speechController.shutdown() }
    }
    var spokenUpTo by remember { mutableIntStateOf(chatMessages.size) }
    LaunchedEffect(chatMessages.size, isAskSheetOpen, speakReplies) {
        if (!isAskSheetOpen) {
            spokenUpTo = chatMessages.size
            return@LaunchedEffect
        }
        while (spokenUpTo < chatMessages.size) {
            val message = chatMessages[spokenUpTo]
            spokenUpTo++
            SpeechPolicy.textToSpeak(message.sender, message.text, muted = !speakReplies)?.let {
                speechController.speak(it)
            }
        }
    }

    // Sends one request to the voice side and adds both sides of the exchange to the conversation
    val sendUtterance: (String) -> Unit = { text ->
        chatMessages.add(ChatMessage(sender = MessageSender.USER, text = text))
        coroutineScope.launch {
            isProcessingQuery = true
            val reply = repo.processUserUtterance(text)
            isProcessingQuery = false
            chatMessages.add(reply)
        }
    }

    val startSpeechRecognition: () -> Unit = {
        speechController.stop()
        if (speechRecognizer != null) {
            speechRecognizer.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) { isListening = true }
                override fun onBeginningOfSpeech() {}
                override fun onRmsChanged(rmsdB: Float) {}
                override fun onBufferReceived(buffer: ByteArray?) {}
                override fun onEndOfSpeech() { isListening = false }
                override fun onError(error: Int) {
                    isListening = false
                    Log.w("MainScreen", "Speech recognition error: $error")
                    val msg = when (error) {
                        SpeechRecognizer.ERROR_NO_MATCH -> "No speech recognized. Please try speaking again."
                        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No speech detected."
                        SpeechRecognizer.ERROR_AUDIO -> "Audio recording error."
                        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission required."
                        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Network error during speech recognition."
                        else -> "Speech recognition error ($error)."
                    }
                    chatMessages.add(ChatMessage(sender = MessageSender.MEANWHILE, text = msg))
                }
                override fun onResults(results: Bundle?) {
                    isListening = false
                    val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    val spokenText = matches?.firstOrNull()?.trim()
                    if (!spokenText.isNullOrBlank()) sendUtterance(spokenText)
                }
                override fun onPartialResults(partialResults: Bundle?) {}
                override fun onEvent(eventType: Int, params: Bundle?) {}
            })

            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            }
            speechRecognizer.startListening(intent)
            isListening = true
        } else {
            chatMessages.add(ChatMessage(sender = MessageSender.MEANWHILE, text = "Voice recognition not available on this device. Please type your request."))
        }
    }

    val recordAudioPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission()
    ) { isGranted ->
        hasMicPermission = isGranted
        if (isGranted) {
            startSpeechRecognition()
        } else {
            chatMessages.add(ChatMessage(sender = MessageSender.MEANWHILE, text = "Microphone permission is needed for voice queries."))
        }
    }

    // Wake word: listens while the app is in the foreground and the switch is on; pauses while the app speaks or the
    // manual mic is in use
    var wakeWordEnabled by remember { mutableStateOf(prefs.wakeWordEnabled) }
    var askedForWakeMic by rememberSaveable { mutableStateOf(false) }
    var wakeRequestListening by remember { mutableStateOf(false) }
    val wakeMicPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission()
    ) { isGranted -> hasMicPermission = isGranted }
    val onWakeHeard by rememberUpdatedState<(String?) -> Unit> { request ->
        isAskSheetOpen = true
        if (request == null) {
            chatMessages.add(ChatMessage(sender = MessageSender.MEANWHILE, text = "Yes?"))
        } else {
            sendUtterance(request)
        }
    }
    val wakeListener = remember {
        WakeWordListener(
            context = context,
            wakeWord = BuildConfig.WAKE_WORD,
            onHeard = { onWakeHeard(it) },
            onRequestListening = { wakeRequestListening = it },
            onMicDenied = { hasMicPermission = false }
        )
    }
    DisposableEffect(wakeListener) {
        onDispose { wakeListener.release() }
    }
    val lifecycleOwner = LocalLifecycleOwner.current
    var inForeground by remember {
        mutableStateOf(lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED))
    }
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) inForeground = true
            if (event == Lifecycle.Event.ON_PAUSE) inForeground = false
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    LaunchedEffect(wakeWordEnabled, hasMicPermission, inForeground) {
        if (wakeWordEnabled && inForeground && !hasMicPermission && !askedForWakeMic) {
            askedForWakeMic = true
            wakeMicPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
    val wakeWordOn = wakeWordEnabled && hasMicPermission && inForeground
    val wakeListening = wakeWordOn && !isListening && !speechController.speaking
    LaunchedEffect(wakeListening) {
        if (wakeListening) wakeListener.start() else wakeListener.stop()
    }

    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentDestination = navBackStackEntry?.destination?.route

    val isTopLevelDestination = currentDestination in listOf(
        Screen.Home.route,
        Screen.Timeline.route,
        Screen.Cameras.route,
        Screen.People.route
    )

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            Column {
                // Screens without the app bar draw their own header: keep it below the status bar so it can be tapped
                if (!isTopLevelDestination) {
                    Spacer(modifier = Modifier.windowInsetsTopHeight(WindowInsets.statusBars))
                }
                if (isTopLevelDestination) {
                    TopAppBar(
                        title = {
                            Text(
                                text = "Meanwhile",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.Bold,
                                color = MeanwhileDeepTeal
                            )
                        },
                        actions = {
                            if (wakeWordOn) {
                                WakeWordChip(wakeWord = BuildConfig.WAKE_WORD, paused = !wakeListening)
                            }
                            IconButton(onClick = { navController.navigate(Screen.Settings.route) }) {
                                Icon(
                                    imageVector = Icons.Default.Settings,
                                    contentDescription = "Settings",
                                    tint = MaterialTheme.colorScheme.onSurface
                                )
                            }
                        },
                        colors = TopAppBarDefaults.topAppBarColors(
                            containerColor = MaterialTheme.colorScheme.surface
                        )
                    )
                }

                if (isUnauthorized) {
                    Surface(
                        color = MeanwhileAlertColors.AlertRedContainer,
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { navController.navigate(Screen.Connect.route) }
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(
                                imageVector = Icons.Default.Warning,
                                contentDescription = null,
                                tint = MeanwhileAlertColors.AlertRed,
                                modifier = Modifier.size(18.dp)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = "Hub access unauthorized or token revoked. Tap to pair again.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MeanwhileAlertColors.AlertOnRedContainer,
                                modifier = Modifier.weight(1f)
                            )
                        }
                    }
                }
            }
        },
        bottomBar = {
            if (isTopLevelDestination) {
                Box {
                    NavigationBar(
                        containerColor = MaterialTheme.colorScheme.surface,
                        tonalElevation = 8.dp
                    ) {
                        NavigationBarItem(
                            selected = currentDestination == Screen.Home.route,
                            onClick = {
                                navController.navigate(Screen.Home.route) {
                                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(Icons.Default.Home, contentDescription = "Home") },
                            label = { Text("Home") },
                            colors = NavigationBarItemDefaults.colors(indicatorColor = MeanwhileDeepTeal.copy(alpha = 0.2f))
                        )
                        NavigationBarItem(
                            selected = currentDestination == Screen.Timeline.route,
                            onClick = {
                                navController.navigate(Screen.Timeline.route) {
                                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(Icons.Default.Timeline, contentDescription = "Timeline") },
                            label = { Text("Timeline") },
                            colors = NavigationBarItemDefaults.colors(indicatorColor = MeanwhileDeepTeal.copy(alpha = 0.2f))
                        )
                        NavigationBarItem(
                            selected = currentDestination == Screen.Cameras.route,
                            onClick = {
                                navController.navigate(Screen.Cameras.route) {
                                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(Icons.Default.Videocam, contentDescription = "Cameras") },
                            label = { Text("Cameras") },
                            colors = NavigationBarItemDefaults.colors(indicatorColor = MeanwhileDeepTeal.copy(alpha = 0.2f))
                        )
                        NavigationBarItem(
                            selected = currentDestination == Screen.People.route,
                            onClick = {
                                navController.navigate(Screen.People.route) {
                                    popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(Icons.Default.People, contentDescription = "People") },
                            label = { Text("People") },
                            colors = NavigationBarItemDefaults.colors(indicatorColor = MeanwhileDeepTeal.copy(alpha = 0.2f))
                        )
                    }
                }
            }
        }
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
        ) {
            NavHost(
                navController = navController,
                startDestination = Screen.Home.route,
                modifier = Modifier.fillMaxSize()
            ) {
                composable(Screen.Home.route) {
                    HomeScreen(
                        session = activeSession,
                        latestEvent = events.firstOrNull(),
                        cameras = cameras,
                        lagSeconds = lagSeconds,
                        repo = repo,
                        onImBackClick = {
                            activeSession?.let { s ->
                                coroutineScope.launch {
                                    try {
                                        repo.endSession(s.id)
                                    } catch (e: Exception) {
                                        snackbarHostState.showSnackbar(e.message ?: "Failed to end session")
                                    }
                                }
                            }
                        },
                        onShowTutorialClick = { showTutorial = true },
                        onCameraClick = {
                            navController.navigate(Screen.Cameras.route)
                        }
                    )
                }

                composable(Screen.Timeline.route) {
                    TimelineScreen(
                        session = activeSession,
                        events = events,
                        onEventClick = { event ->
                            navController.navigate(Screen.EventDetail.createRoute(event.id))
                        },
                        onImBackClick = {
                            activeSession?.let { s ->
                                coroutineScope.launch {
                                    try {
                                        repo.endSession(s.id)
                                    } catch (e: Exception) {
                                        snackbarHostState.showSnackbar(e.message ?: "Failed to end session")
                                    }
                                }
                            }
                        }
                    )
                }

                composable(Screen.Cameras.route) {
                    CamerasScreen(
                        cameras = cameras,
                        session = activeSession,
                        repo = repo
                    )
                }

                composable(Screen.People.route) {
                    PeopleScreen(
                        people = people,
                        onAddPerson = { name, role, photoUris, note ->
                            repo.addPerson(name, role, photoUris, note)
                        },
                        onDeletePerson = { id ->
                            repo.deletePerson(id)
                        },
                        onNamePerson = { id, name, role ->
                            repo.namePerson(id, name, role)
                        },
                        onMergePerson = { id, intoId ->
                            repo.mergePerson(id, intoId)
                        },
                        onDeleteAllUnnamed = {
                            repo.deleteAllUnnamed()
                        }
                    )
                }

                composable(Screen.Connect.route) {
                    ConnectScreen(
                        currentAddress = prefs.hubAddress,
                        currentKey = prefs.apiKey,
                        repo = repo,
                        onConnectSuccess = { address, key, useHttps ->
                            prefs.hubAddress = address
                            prefs.apiKey = key
                            if (useHttps != null) {
                                prefs.useHttps = useHttps
                            }
                            app.registerPushDevice()
                            navController.navigate(Screen.Home.route) {
                                popUpTo(Screen.Home.route) { inclusive = true }
                            }
                        }
                    )
                }

                composable(Screen.Settings.route) {
                    SettingsScreen(
                        prefs = prefs,
                        repo = repo,
                        onBackClick = { navController.popBackStack() },
                        onRePairClick = { navController.navigate(Screen.Connect.route) },
                        onPastSessionsClick = { navController.navigate(Screen.PastSessions.route) },
                        onShowTutorialClick = { showTutorial = true },
                        wakeWordEnabled = wakeWordEnabled,
                        onWakeWordChange = {
                            wakeWordEnabled = it
                            prefs.wakeWordEnabled = it
                        }
                    )
                }

                composable(Screen.PastSessions.route) {
                    PastSessionsScreen(
                        sessions = pastSessions,
                        onBackClick = { navController.popBackStack() }
                    )
                }

                composable(Screen.EventDetail.route) { backStackEntry ->
                    val eventId = backStackEntry.arguments?.getString("eventId")
                    val currentEvent = events.find { it.id == eventId }
                        ?: if (eventId.isNullOrBlank()) events.firstOrNull() else null

                    if (currentEvent == null) {
                        Column(
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(24.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center
                        ) {
                            Text(
                                text = "Event not found",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold
                            )
                            Spacer(modifier = Modifier.height(8.dp))
                            Text(
                                text = "The requested event could not be found or has not loaded yet.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                            Spacer(modifier = Modifier.height(16.dp))
                            Button(
                                onClick = { navController.popBackStack() },
                                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                            ) {
                                Text("Back")
                            }
                        }
                    } else {
                        val currentEventIndex = events.indexOf(currentEvent)
                        EventDetailScreen(
                            event = currentEvent,
                            people = people,
                            hasPrevious = currentEventIndex > 0,
                            hasNext = currentEventIndex >= 0 && currentEventIndex < events.size - 1,
                            onPreviousClick = {
                                if (currentEventIndex > 0) {
                                    navController.navigate(Screen.EventDetail.createRoute(events[currentEventIndex - 1].id))
                                }
                            },
                            onNextClick = {
                                if (currentEventIndex >= 0 && currentEventIndex < events.size - 1) {
                                    navController.navigate(Screen.EventDetail.createRoute(events[currentEventIndex + 1].id))
                                }
                            },
                            onBackClick = { navController.popBackStack() },
                            onJumpToLiveClick = {
                                navController.navigate(Screen.Cameras.route)
                            }
                        )
                    }
                }
            }

            // Ask bar pinned above the bottom tabs
            if (isTopLevelDestination) {
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(horizontal = 16.dp, vertical = 8.dp)
                        .fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .shadow(6.dp, RoundedCornerShape(28.dp))
                            .clip(RoundedCornerShape(28.dp))
                            .background(MaterialTheme.colorScheme.surface)
                            .clickable { isAskSheetOpen = true }
                            .padding(horizontal = 16.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(CircleShape)
                                .background(MeanwhileDeepTeal),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(
                                imageVector = Icons.Default.Mic,
                                contentDescription = "Mic",
                                tint = Color.White,
                                modifier = Modifier.size(20.dp)
                            )
                        }
                        Spacer(modifier = Modifier.width(12.dp))
                        Text(
                            text = "Ask Meanwhile or tell it what to watch",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.outline
                        )
                    }
                }
            }

            if (isAskSheetOpen) {
                AskBottomSheet(
                    sheetState = askSheetState,
                    messages = chatMessages,
                    isListening = isListening || wakeRequestListening,
                    isProcessing = isProcessingQuery,
                    speakReplies = speakReplies,
                    speechUnavailable = speechController.unavailable,
                    onToggleSpeak = {
                        speakReplies = !speakReplies
                        prefs.speakReplies = speakReplies
                        if (!speakReplies) speechController.stop()
                    },
                    onDismissRequest = {
                        speechController.stop()
                        isAskSheetOpen = false
                    },
                    onSendUtterance = sendUtterance,
                    onToggleMic = {
                        speechController.stop()
                        wakeListener.stop()
                        if (isListening) {
                            speechRecognizer?.stopListening()
                            isListening = false
                        } else {
                            if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                                startSpeechRecognition()
                            } else {
                                recordAudioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                            }
                        }
                    },
                    onSelectOption = { option ->
                        chatMessages.add(ChatMessage(sender = MessageSender.USER, text = option))
                        coroutineScope.launch {
                            isProcessingQuery = true
                            val reply = repo.processUserUtterance("Use $option camera")
                            isProcessingQuery = false
                            chatMessages.add(reply)
                        }
                    }
                )
            }
        }
    }

    if (showTutorial) {
        BackHandler { showTutorial = false; prefs.tutorialSeen = true }
        TutorialScreen(
            onFinish = {
                prefs.tutorialSeen = true
                showTutorial = false
            }
        )
    }

    var consumedNonce by rememberSaveable { mutableStateOf<Long?>(null) }

    // Opens a notification's event once the NavHost exists; each tap is handled once
    LaunchedEffect(deepLinkAction) {
        if (deepLinkAction != null && deepLinkAction.nonce != consumedNonce) {
            consumedNonce = deepLinkAction.nonce
            onDeepLinkConsumed(deepLinkAction.nonce)
            navController.navigate(Screen.EventDetail.createRoute(deepLinkAction.eventId))
        }
    }
}
