// The People tab: who Meanwhile recognises, unnamed faces to name, and adding people with face photos.
package com.meanwhile.android.ui.people

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Merge
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material.icons.filled.VerifiedUser
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.meanwhile.android.model.NamePersonResult
import com.meanwhile.android.model.Person
import com.meanwhile.android.ui.components.MediaImage
import com.meanwhile.android.ui.theme.MeanwhileAlertColors
import com.meanwhile.android.ui.theme.MeanwhileDeepTeal
import kotlinx.coroutines.launch

/** The People tab: unnamed faces to name, the recognised people, and adding a person with photos. */
@Composable
fun PeopleScreen(
    people: List<Person>,
    onAddPerson: suspend (name: String, role: String, photoUris: List<String>, note: String?) -> Unit,
    onDeletePerson: suspend (String) -> Unit,
    onNamePerson: suspend (id: String, name: String, role: String?) -> NamePersonResult,
    onMergePerson: suspend (id: String, intoId: String) -> Unit,
    onDeleteAllUnnamed: suspend () -> Unit,
    modifier: Modifier = Modifier
) {
    val coroutineScope = rememberCoroutineScope()
    val snackbarHostState = remember { SnackbarHostState() }

    var showAddDialog by remember { mutableStateOf(false) }
    var namingPerson by remember { mutableStateOf<Person?>(null) }
    // (the unnamed person, the id of the person who already has that name)
    var mergeConflict by remember { mutableStateOf<Pair<Person, String>?>(null) }
    var showClearUnnamedConfirm by remember { mutableStateOf(false) }

    val unnamedPeople = remember(people) { people.filter { it.unnamed } }
    val namedPeople = remember(people) { people.filterNot { it.unnamed } }

    Box(modifier = modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp, vertical = 8.dp)
        ) {
            Text(
                text = "People & Face ID",
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface
            )
            Text(
                text = "People Meanwhile can name in heads-ups, verdicts and activity",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(modifier = Modifier.height(14.dp))

            LazyColumn(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(16.dp),
                contentPadding = PaddingValues(bottom = 90.dp)
            ) {
                // Unnamed faces
                if (unnamedPeople.isNotEmpty()) {
                    item {
                        Card(
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(16.dp),
                            colors = CardDefaults.cardColors(
                                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
                            )
                        ) {
                            Column(modifier = Modifier.padding(14.dp)) {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Icon(
                                            imageVector = Icons.Default.Face,
                                            contentDescription = null,
                                            tint = MeanwhileDeepTeal,
                                            modifier = Modifier.size(20.dp)
                                        )
                                        Spacer(modifier = Modifier.width(8.dp))
                                        Text(
                                            text = "Faces to Name (${unnamedPeople.size})",
                                            style = MaterialTheme.typography.titleMedium,
                                            fontWeight = FontWeight.Bold,
                                            color = MaterialTheme.colorScheme.onSurface
                                        )
                                    }

                                    TextButton(onClick = { showClearUnnamedConfirm = true }) {
                                        Text("Clear All", color = MaterialTheme.colorScheme.error)
                                    }
                                }

                                Text(
                                    text = "Face ID detected visitors who don't match anyone. Name them so Meanwhile recognises them.",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )

                                Spacer(modifier = Modifier.height(12.dp))

                                LazyRow(
                                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                                ) {
                                    items(unnamedPeople) { person ->
                                        UnnamedPersonTile(
                                            person = person,
                                            onNameClick = { namingPerson = person },
                                            onDismissClick = {
                                                coroutineScope.launch {
                                                    try {
                                                        onDeletePerson(person.displayId)
                                                    } catch (e: Exception) {
                                                        snackbarHostState.showSnackbar(e.message ?: "Failed to delete person")
                                                    }
                                                }
                                            }
                                        )
                                    }
                                }
                            }
                        }
                    }
                }

                // Recognised people
                item {
                    Text(
                        text = "Recognised People (${namedPeople.size})",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                }

                if (namedPeople.isEmpty()) {
                    item {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 24.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = "No recognised people yet. Tap + to add family, helpers or guests.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.outline
                            )
                        }
                    }
                } else {
                    items(namedPeople) { person ->
                        PersonCard(
                            person = person,
                            onDeleteClick = {
                                coroutineScope.launch {
                                    try {
                                        onDeletePerson(person.displayId)
                                    } catch (e: Exception) {
                                        snackbarHostState.showSnackbar(e.message ?: "Failed to delete person")
                                    }
                                }
                            }
                        )
                    }
                }

                // Consent note
                item {
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(12.dp),
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f)
                        )
                    ) {
                        Column(modifier = Modifier.padding(14.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(
                                    imageVector = Icons.Default.VerifiedUser,
                                    contentDescription = null,
                                    tint = MeanwhileDeepTeal,
                                    modifier = Modifier.size(18.dp)
                                )
                                Spacer(modifier = Modifier.width(6.dp))
                                Text(
                                    text = "Consent & privacy",
                                    style = MaterialTheme.typography.labelLarge,
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onSurface
                                )
                            }
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = "Only add people who have consented to being recognised. Unnamed faces not seen for several days are automatically purged.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }
        }

        FloatingActionButton(
            onClick = { showAddDialog = true },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(end = 16.dp, bottom = 80.dp),
            containerColor = MeanwhileDeepTeal,
            contentColor = Color.White
        ) {
            Icon(imageVector = Icons.Default.Add, contentDescription = "Add Person")
        }

        SnackbarHost(
            hostState = snackbarHostState,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 80.dp)
        )
    }

    // Name an unnamed face
    if (namingPerson != null) {
        NameFaceDialog(
            person = namingPerson!!,
            onDismiss = { namingPerson = null },
            onConfirm = { name, role ->
                val targetPerson = namingPerson!!
                namingPerson = null
                coroutineScope.launch {
                    when (val result = onNamePerson(targetPerson.displayId, name, role)) {
                        is NamePersonResult.Success -> {
                            snackbarHostState.showSnackbar("Named as ${result.person.name}")
                        }
                        is NamePersonResult.NameTaken -> {
                            mergeConflict = Pair(targetPerson, result.existingPersonId)
                        }
                        is NamePersonResult.Failure -> {
                            snackbarHostState.showSnackbar(result.message)
                        }
                    }
                }
            }
        )
    }

    // The name belongs to someone else: offer to merge (409 name_taken)
    if (mergeConflict != null) {
        val (unnamedPerson, existingId) = mergeConflict!!
        val existingPerson = namedPeople.find { it.displayId == existingId || it.name.equals(unnamedPerson.name, true) }
        val targetName = existingPerson?.name ?: "existing person"

        AlertDialog(
            onDismissRequest = { mergeConflict = null },
            icon = { Icon(Icons.Default.Merge, contentDescription = null, tint = MeanwhileDeepTeal) },
            title = { Text("Person Already Exists") },
            text = {
                Text(
                    text = "You already have '$targetName' in your recognised list. Is this face the same person? Merging will combine their photos, face ID models, and past activity."
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        val unId = unnamedPerson.displayId
                        mergeConflict = null
                        coroutineScope.launch {
                            try {
                                onMergePerson(unId, existingId)
                                snackbarHostState.showSnackbar("Merged with $targetName")
                            } catch (e: Exception) {
                                snackbarHostState.showSnackbar(e.message ?: "Merge failed")
                            }
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
                ) {
                    Text("Yes, Merge")
                }
            },
            dismissButton = {
                TextButton(onClick = { mergeConflict = null }) {
                    Text("No, Choose Different Name")
                }
            }
        )
    }

    // Clear all unnamed faces
    if (showClearUnnamedConfirm) {
        AlertDialog(
            onDismissRequest = { showClearUnnamedConfirm = false },
            title = { Text("Clear All Unnamed Faces?") },
            text = {
                Text("This will delete all temporary face ID profiles for unnamed visitors. They will be detected as new visitors next time.")
            },
            confirmButton = {
                Button(
                    onClick = {
                        showClearUnnamedConfirm = false
                        coroutineScope.launch {
                            try {
                                onDeleteAllUnnamed()
                                snackbarHostState.showSnackbar("All unnamed faces cleared")
                            } catch (e: Exception) {
                                snackbarHostState.showSnackbar("Failed to clear: ${e.message}")
                            }
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
                ) {
                    Text("Clear All")
                }
            },
            dismissButton = {
                TextButton(onClick = { showClearUnnamedConfirm = false }) {
                    Text("Cancel")
                }
            }
        )
    }

    // Add a person
    if (showAddDialog) {
        AddPersonDialog(
            onDismiss = { showAddDialog = false },
            onConfirm = { name, role, photoUris, note ->
                onAddPerson(name, role, photoUris, note)
                showAddDialog = false
                snackbarHostState.showSnackbar("Added $name")
            }
        )
    }
}

/** One unnamed face: its photo, where it was seen, Name and dismiss buttons. */
@Composable
fun UnnamedPersonTile(
    person: Person,
    onNameClick: () -> Unit,
    onDismissClick: () -> Unit
) {
    ElevatedCard(
        modifier = Modifier.width(160.dp),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.elevatedCardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Column(modifier = Modifier.padding(10.dp)) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(1f)
                    .clip(RoundedCornerShape(10.dp))
                    .background(Color.DarkGray)
            ) {
                if (person.photoUrl != null) {
                    MediaImage(
                        url = person.photoUrl,
                        contentDescription = person.name,
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop
                    )
                } else {
                    Icon(
                        imageVector = Icons.Default.Face,
                        contentDescription = null,
                        tint = Color.LightGray,
                        modifier = Modifier
                            .align(Alignment.Center)
                            .size(48.dp)
                    )
                }

                IconButton(
                    onClick = onDismissClick,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(4.dp)
                        .size(24.dp)
                        .background(Color.Black.copy(alpha = 0.6f), CircleShape)
                ) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = "Dismiss",
                        tint = Color.White,
                        modifier = Modifier.size(14.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = person.name.ifEmpty { "Unnamed Person" },
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface,
                maxLines = 1
            )
            Text(
                text = if (person.cameraId != null) "on ${person.cameraId}" else "detected",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1
            )

            Spacer(modifier = Modifier.height(10.dp))

            Button(
                onClick = onNameClick,
                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal),
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(vertical = 6.dp)
            ) {
                Text("Name", style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

/** One recognised person: photo, name, role, face count and note, with a delete button. */
@Composable
fun PersonCard(
    person: Person,
    onDeleteClick: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(54.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.surfaceVariant)
            ) {
                if (person.photoUrl != null) {
                    MediaImage(
                        url = person.photoUrl,
                        contentDescription = person.name,
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop
                    )
                } else {
                    Icon(
                        imageVector = Icons.Default.Face,
                        contentDescription = null,
                        tint = Color.Gray,
                        modifier = Modifier
                            .align(Alignment.Center)
                            .size(32.dp)
                    )
                }
            }

            Spacer(modifier = Modifier.width(14.dp))

            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = if (person.isOwner) "${person.name} (You)" else person.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                }

                Spacer(modifier = Modifier.height(4.dp))

                Row(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .clip(RoundedCornerShape(6.dp))
                            .background(
                                when {
                                    person.isOwner -> MeanwhileDeepTeal.copy(alpha = 0.15f)
                                    person.role.equals("Helper", true) -> MeanwhileAlertColors.HeadsUpAmberContainer
                                    else -> MaterialTheme.colorScheme.surfaceVariant
                                }
                            )
                            .padding(horizontal = 8.dp, vertical = 2.dp)
                    ) {
                        Text(
                            text = person.role.replaceFirstChar { it.uppercase() },
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = FontWeight.Medium,
                            color = when {
                                person.isOwner -> MeanwhileDeepTeal
                                person.role.equals("Helper", true) -> MeanwhileAlertColors.HeadsUpOnAmberContainer
                                else -> MaterialTheme.colorScheme.onSurfaceVariant
                            }
                        )
                    }

                    if (person.faces > 0) {
                        Text(
                            text = "${person.faces} ${if (person.faces == 1) "face" else "faces"}",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.outline
                        )
                    }
                }

                if (!person.note.isNullOrBlank()) {
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = person.note,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            IconButton(onClick = onDeleteClick) {
                Icon(
                    imageVector = Icons.Default.Delete,
                    contentDescription = "Delete Person",
                    tint = MaterialTheme.colorScheme.outline
                )
            }
        }
    }
}

/** Asks for a name and role for an unnamed face. */
@Composable
fun NameFaceDialog(
    person: Person,
    onDismiss: () -> Unit,
    onConfirm: (name: String, role: String) -> Unit
) {
    var name by remember { mutableStateOf("") }
    var selectedRoleIndex by remember { mutableIntStateOf(0) }
    val roles = listOf("Family", "Helper", "Relative", "Friend", "Guest")

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(text = "Name this Person") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                if (person.photoUrl != null) {
                    Box(
                        modifier = Modifier
                            .size(80.dp)
                            .align(Alignment.CenterHorizontally)
                            .clip(CircleShape)
                            .border(2.dp, MeanwhileDeepTeal, CircleShape)
                    ) {
                        MediaImage(
                            url = person.photoUrl,
                            contentDescription = person.name,
                            modifier = Modifier.fillMaxSize(),
                            contentScale = ContentScale.Crop
                        )
                    }
                }

                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Name (e.g. Alex, Sam, Helper)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                Text(text = "Role", style = MaterialTheme.typography.labelLarge)
                SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                    roles.take(3).forEachIndexed { index, roleName ->
                        SegmentedButton(
                            shape = SegmentedButtonDefaults.itemShape(index = index, count = 3),
                            onClick = { selectedRoleIndex = index },
                            selected = index == selectedRoleIndex
                        ) {
                            Text(roleName, maxLines = 1)
                        }
                    }
                }
                SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                    roles.drop(3).forEachIndexed { index, roleName ->
                        val actualIndex = index + 3
                        SegmentedButton(
                            shape = SegmentedButtonDefaults.itemShape(index = index, count = 2),
                            onClick = { selectedRoleIndex = actualIndex },
                            selected = actualIndex == selectedRoleIndex
                        ) {
                            Text(roleName, maxLines = 1)
                        }
                    }
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (name.isNotBlank()) {
                        onConfirm(name.trim(), roles[selectedRoleIndex])
                    }
                },
                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
            ) {
                Text("Save")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel")
            }
        }
    )
}

/** Adds a person: name, role, one to three face photos and an optional note; shows the core's error if it refuses. */
@Composable
fun AddPersonDialog(
    onDismiss: () -> Unit,
    onConfirm: suspend (name: String, role: String, photoUris: List<String>, note: String?) -> Unit
) {
    var name by remember { mutableStateOf("") }
    val roles = listOf("Family", "Helper", "Relative", "Friend", "Guest", "Owner")
    var selectedRoleIndex by remember { mutableIntStateOf(0) }
    var note by remember { mutableStateOf("") }
    val selectedPhotoUris = remember { mutableStateListOf<String>() }
    var isSubmitting by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    val coroutineScope = rememberCoroutineScope()

    // System photo picker, up to 3 photos
    val photoPickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.PickMultipleVisualMedia(3)
    ) { uris ->
        selectedPhotoUris.clear()
        uris.take(3).forEach { uri ->
            selectedPhotoUris.add(uri.toString())
        }
    }

    AlertDialog(
        onDismissRequest = { if (!isSubmitting) onDismiss() },
        title = {
            Text(text = "Add Recognised Person", style = MaterialTheme.typography.titleLarge)
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                if (!errorMessage.isNullOrBlank()) {
                    Card(
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.errorContainer
                        ),
                        shape = RoundedCornerShape(8.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Row(
                            modifier = Modifier.padding(10.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(
                                imageVector = Icons.Default.Warning,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.error,
                                modifier = Modifier.size(20.dp)
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = errorMessage!!,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onErrorContainer
                            )
                        }
                    }
                }

                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Full Name") },
                    singleLine = true,
                    enabled = !isSubmitting,
                    modifier = Modifier.fillMaxWidth()
                )

                Text(text = "Role", style = MaterialTheme.typography.labelLarge)
                SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                    roles.take(3).forEachIndexed { index, roleName ->
                        SegmentedButton(
                            shape = SegmentedButtonDefaults.itemShape(index = index, count = 3),
                            onClick = { selectedRoleIndex = index },
                            selected = index == selectedRoleIndex,
                            enabled = !isSubmitting
                        ) {
                            Text(roleName, maxLines = 1)
                        }
                    }
                }
                SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
                    roles.drop(3).forEachIndexed { index, roleName ->
                        val actualIndex = index + 3
                        SegmentedButton(
                            shape = SegmentedButtonDefaults.itemShape(index = index, count = 3),
                            onClick = { selectedRoleIndex = actualIndex },
                            selected = actualIndex == selectedRoleIndex,
                            enabled = !isSubmitting
                        ) {
                            Text(roleName, maxLines = 1)
                        }
                    }
                }

                Text(
                    text = "Face ID Photos (1–3 photos)",
                    style = MaterialTheme.typography.labelLarge
                )

                if (selectedPhotoUris.isNotEmpty()) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        selectedPhotoUris.forEachIndexed { index, uriStr ->
                            Box(
                                modifier = Modifier
                                    .size(64.dp)
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(Color.DarkGray)
                            ) {
                                AsyncImage(
                                    model = uriStr,
                                    contentDescription = "Photo ${index + 1}",
                                    modifier = Modifier.fillMaxSize(),
                                    contentScale = ContentScale.Crop
                                )
                                IconButton(
                                    onClick = { selectedPhotoUris.removeAt(index) },
                                    modifier = Modifier
                                        .align(Alignment.TopEnd)
                                        .size(20.dp)
                                        .background(Color.Black.copy(alpha = 0.7f), CircleShape)
                                ) {
                                    Icon(
                                        imageVector = Icons.Default.Close,
                                        contentDescription = "Remove",
                                        tint = Color.White,
                                        modifier = Modifier.size(12.dp)
                                    )
                                }
                            }
                        }
                    }
                }

                OutlinedButton(
                    onClick = {
                        photoPickerLauncher.launch(
                            PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)
                        )
                    },
                    enabled = !isSubmitting,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Icon(imageVector = Icons.Default.PhotoLibrary, contentDescription = null)
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(if (selectedPhotoUris.isEmpty()) "Choose 1–3 Photos" else "Change Photos (${selectedPhotoUris.size}/3)")
                }

                Text(
                    text = "Tip: Front-facing photos without sunglasses work best for Face ID recognition.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                OutlinedTextField(
                    value = note,
                    onValueChange = { note = it },
                    label = { Text("Notes (optional description, e.g. blue jacket)") },
                    singleLine = true,
                    enabled = !isSubmitting,
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (name.isNotBlank()) {
                        isSubmitting = true
                        errorMessage = null
                        coroutineScope.launch {
                            try {
                                onConfirm(
                                    name.trim(),
                                    roles[selectedRoleIndex].lowercase(),
                                    selectedPhotoUris.toList(),
                                    note.ifBlank { null }
                                )
                            } catch (e: Exception) {
                                isSubmitting = false
                                errorMessage = e.message ?: "Failed to add person"
                            }
                        }
                    }
                },
                enabled = name.isNotBlank() && !isSubmitting,
                colors = ButtonDefaults.buttonColors(containerColor = MeanwhileDeepTeal)
            ) {
                if (isSubmitting) {
                    CircularProgressIndicator(color = Color.White, modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                } else {
                    Text("Add")
                }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !isSubmitting) {
                Text("Cancel")
            }
        }
    )
}
